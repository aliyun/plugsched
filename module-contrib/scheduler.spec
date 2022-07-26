# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

%define _prefix /usr/local
%define minor_name xxx
%define release yyy

Name:		scheduler-%{minor_name}
Version:	%{KVER}
Release:	%{KREL}.%{release}
Summary:	The schedule policy RPM for linux kernel scheduler subsystem
BuildRequires:	elfutils-devel
BuildRequires:	systemd
Requires:	systemd
Requires:	binutils
Requires:	cpio
Packager:	Yihao Wu <wuyihao@linux.alibaba.com>

Group:		System Environment/Kernel
License:	GPLv2
URL:		None

%description
The scheduler policy rpm-package.

%prep
# copy files to rpmbuild/SOURCE/
cp %{_outdir}/* %{_sourcedir}
cp %{_tmpdir}/boundary.yaml %{_sourcedir}

chmod 0644 %{_sourcedir}/{version,boundary.yaml}
rm -f %{_sourcedir}/scheduler.spec

%build
# Build sched_mod
make KBUILD_MODPOST_WARN=1 plugsched_tmpdir=%{_tmpdir} plugsched_modpath=%{_modpath} \
	-C %{_kerneldir} -f %{_tmpdir}/Makefile.plugsched plugsched -j %{threads}

# Build symbol resolve tool
make -C %{_tmpdir}/symbol_resolve

# Generate the tainted_functions file
awk -F '[(,)]' '$2!=""{print $2" "$3" vmlinux"}' %{_modpath}/tainted_functions.h > %{_sourcedir}/tainted_functions
chmod 0444 %{_sourcedir}/tainted_functions

%install
#install tool, module and systemd service
mkdir -p %{buildroot}%{_prefix}/lib/systemd/system
mkdir -p %{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}

install -m 755 %{_tmpdir}/symbol_resolve/symbol_resolve %{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/symbol_resolve
install -m 755 %{_modpath}/scheduler.ko %{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/scheduler.ko
install -m 644 %{_sourcedir}/plugsched.service %{buildroot}%{_prefix}/lib/systemd/system

cp %{_sourcedir}/* %{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}
rm -f %{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/plugsched.service

#install kernel module after install this rpm-package
%post
sync

if [ "$(uname -r)" != "%{KVER}-%{KREL}.%{_arch}" ]; then
	echo "INFO: scheduler dose not match kernel, skip load module..."
	exit 0
fi

echo "Start plugsched.service"
systemctl daemon-reload
systemctl enable plugsched
systemctl start plugsched

#uninstall kernel module before remove this rpm-package
%preun
if [ "$(uname -r)" != "%{KVER}-%{KREL}.%{_arch}" ]; then
	echo "INFO: scheduler dose not match kernel, skip unload module..."
	exit 0
fi

echo "Stop plugsched.service"
/var/plugsched/$(uname -r)/scheduler-installer uninstall || exit 1

%postun
systemctl daemon-reload

%files
%{_prefix}/lib/systemd/system/plugsched.service
%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/*

%dir
%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}

%changelog
