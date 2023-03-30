# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

%define minor_name xxx
%define release yyy
%define _modpath kernel/sched/mod

Name:		scheduler-%{minor_name}
Version:	%{KVER}
Release:	%{KREL}.%{release}
Summary:	The schedule policy RPM for linux kernel scheduler subsystem
Packager:	None

Group:		System Environment/Kernel
License:	GPLv2
URL:		None

BuildRequires:	make, gcc-c++, bc, bison, flex, openssl, openssl-devel
BuildRequires:	glibc-static, zlib-static, libstdc++-static
BuildRequires:	elfutils-devel, elfutils-devel-static, elfutils-libelf-devel

Requires:	systemd
Requires:	binutils

%description
The scheduler policy rpm-package.

%prep

%build
# Build sched_mod
make KBUILD_MODPOST_WARN=1 \
     plugsched_tmpdir=working \
     plugsched_modpath=%{_modpath} \
     sidecar_objs=%{?_sdcrobjs} \
     -C . -f working/Makefile.plugsched \
     plugsched -j $(nproc)

# Build symbol resolve tool
make -C working/symbol_resolve

# Generate the tainted_functions file
awk -F '[(,)]' '$2!=""{print $2" "$3" vmlinux"}' %{_modpath}/tainted_functions.h > working/tainted_functions

%install
#install tool, module and systemd service
mkdir -p %{buildroot}/usr/lib/systemd/system
mkdir -p %{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}

install -m 644 working/plugsched.service \
	%{buildroot}/usr/lib/systemd/system/plugsched.service

install -m 755 working/symbol_resolve/symbol_resolve \
	%{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/symbol_resolve

install -m 755 %{_modpath}/scheduler.ko \
	%{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/scheduler.ko

install -m 444 working/tainted_functions \
	%{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/tainted_functions

install -m 444 working/boundary.yaml \
	%{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/boundary.yaml

install -m 755 working/scheduler-installer \
	%{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/scheduler-installer

install -m 755 working/hotfix_conflict_check \
	%{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/hotfix_conflict_check

install -m 444 working/version \
	%{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/version

#install kernel module after install this rpm-package
%post
sync

if [ "$(uname -r)" != "%{KVER}-%{KREL}.%{_arch}" ]; then
	echo "INFO: scheduler does not match current kernel version, skip starting service ..."
	exit 0
fi

echo "Start plugsched.service"
systemctl enable plugsched
systemctl start plugsched

#uninstall kernel module before remove this rpm-package
%preun
if [ "$(uname -r)" != "%{KVER}-%{KREL}.%{_arch}" ]; then
	echo "INFO: scheduler does not match current kernel version, skip unloading module..."
	exit 0
fi

echo "Stop plugsched.service"
/var/plugsched/$(uname -r)/scheduler-installer uninstall || exit 1
systemctl stop plugsched

%postun
systemctl reset-failed plugsched

%files
%dir %{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}
/usr/lib/systemd/system/plugsched.service
%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/*

%changelog
