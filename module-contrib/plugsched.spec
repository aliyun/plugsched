%define _prefix /usr/local

Name:		plugsched
Version:	%{KVER}
Release:	%{KREL}.3
Summary:	The plugsched rpm
BuildRequires:	elfutils-devel
BuildRequires:	systemd
Requires:	systemd
Requires:	binutils
Requires:	cpio
Packager:	Yihao Wu <wuyihao@linux.alibaba.com>

Group:		System Environment/Kernel
License:	GPLv2
URL:		None
Source1:	plugsched-install
Source3:	plugsched.service

%description
The plugsched rpm-package.

%prep
# copy files to rpmbuild/SOURCE/
cp %{_outdir}/plugsched-install %{_sourcedir}
cp %{_outdir}/plugsched.service %{_sourcedir}

%build
# Build symbol resolve tool
cd %{_dependdir}/tools/symbol_resolve
make srctree=%{_kerneldir}

# Build sched_mod
cd %{_kerneldir}
make -f Makefile.plugsched plugsched -j %{threads}

#%pre
# TODO: confict check

%install
#install the plugsched tool and plugsched-install script and systemd service
mkdir -p %{buildroot}%{_bindir}
mkdir -p %{buildroot}%{_prefix}/lib/systemd/system
mkdir -p %{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}
mkdir -p %{buildroot}%{_rundir}/plugsched

install -m 755 %{_dependdir}/tools/symbol_resolve/symbol_resolve %{buildroot}%{_bindir}/symbol_resolve
install -m 755 %{_kerneldir}/kernel/sched/mod/plugsched.ko %{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/plugsched.ko
install -m 755 %{_kerneldir}/tainted_functions %{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/tainted_functions

install -m 755 %{SOURCE1} %{buildroot}%{_bindir}
install -m 755 %{SOURCE3} %{buildroot}%{_prefix}/lib/systemd/system

#install plugsched module after install this rpm-package
%post
if [ $1 == 1 ];  then
	echo "Installing plugsched."
	systemctl daemon-reload
	systemctl enable plugsched
	systemctl start plugsched
elif [ $1 == 2 ];  then
	echo "Upgrading plugsched - install new version."
	/sbin/rmmod plugsched || echo "plugsched module not loaded. Skip rmmod and continue upgrade."
fi

#uninstall plugsched module before remove this rpm-package
%postun
systemctl daemon-reload
if [ $1 == 0 ]; then
	echo "Uninstalling plugsched."
	/sbin/rmmod plugsched || echo "plugsched module not loaded. Skip rmmod and continue uninstall."
elif [ $1 == 1 ]; then
	echo "Upgrading plugsched - uninstall old version."
	systemctl start plugsched
fi

%files
%{_bindir}/symbol_resolve
%{_bindir}/plugsched-install
%{_prefix}/lib/systemd/system/plugsched.service
%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/plugsched.ko
%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/tainted_functions

%dir
%{_rundir}/plugsched

%changelog
