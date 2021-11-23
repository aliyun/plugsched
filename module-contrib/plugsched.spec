%define dist .al7
%define sha 384f26418c1eff9a48ae337ec5cb69ffe7f8a857
%define kerneltarball 4.19-%{sha}-%{sha}
%define KVER 4.19.91
%define KREL 1
%define _prefix /usr/local

Name:		plugsched-ant
Version:	%{KVER}
Release:	%{KREL}.3
Summary:	The plugsched rpm
BuildRequires:	elfutils-devel
BuildRequires:	systemd
BuildRequires:	yum-plugin-pre-transaction-actions
BuildRequires:	kernel-devel = %{KVER}-%{KREL}
Requires:	systemd
Requires:	binutils
Requires:	cpio
Packager:	Yihao Wu <wuyihao@linux.alibaba.com>

Group:		System Environment/Kernel
License:	GPLv2
URL:		None
Source0:	%{sha}.tar.gz
Source1:	plugsched-install
Source2:	plugsched-uninstall
Source3:	plugsched.service
Source4:	plugsched-tmp.conf
Source5:	plugsched.action
Source6:	plugsched-actions.conf
Source7:	plugsched-verses-kpatch.py
Source8:	extract-abi-black.py
Patch0: 	plugsched-actions.patch

%description
The plugsched rpm-package.

%prep
%setup -q -n kernel-%{kerneltarball}
cp /usr/src/kernels/%{KVER}-%{KREL}.%{_arch}/Module.symvers Module.symvers
cp /usr/lib/yum-plugins/pre-transaction-actions.py plugsched-actions.py
%patch0 -p1

%build
# Build prepare target
make prepare scripts
# Build symbol resolve tool
make tools/plugsched
# Build sched_mod
LOCALVERSION=-%{KREL}.%{_arch} %make_build -f Makefile.plugsched plugsched
# Extract abi blacklist
python %{SOURCE8} sched_boundary_extract.yaml > abi_blacklist

%install
#install the plugsched tool and plugsched-install script and systemd service
mkdir -p %{buildroot}%{_bindir}
mkdir -p %{buildroot}%{_prefix}/lib/systemd/system
mkdir -p %{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}
mkdir -p %{buildroot}%{_rundir}/plugsched
mkdir -p %{buildroot}%{_tmpfilesdir}
mkdir -p %{buildroot}%{_sysconfdir}/yum/plugsched-actions
mkdir -p %{buildroot}%{_sysconfdir}/yum/pluginconf.d
mkdir -p %{buildroot}/usr/lib/yum-plugins

install -m 755 tools/plugsched/symbol_resolve %{buildroot}%{_bindir}/plugsched
install -m 755 kernel/sched/mod/plugsched.ko %{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/plugsched.ko
install -m 755 abi_blacklist %{buildroot}%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/abi_blacklist
install -m 755 %{SOURCE1} %{buildroot}%{_bindir}
install -m 755 %{SOURCE2} %{buildroot}%{_bindir}
install -m 755 %{SOURCE3} %{buildroot}%{_prefix}/lib/systemd/system
install -m 755 %{SOURCE4} %{buildroot}%{_tmpfilesdir}/plugsched.conf
install -m 755 %{SOURCE5} %{buildroot}%{_sysconfdir}/yum/plugsched-actions/
install -m 755 %{SOURCE6} %{buildroot}%{_sysconfdir}/yum/pluginconf.d/
install -m 755 plugsched-actions.py %{buildroot}/usr/lib/yum-plugins/
install -m 755 %{SOURCE7} %{buildroot}%{_bindir}

#install plugsched module after install this rpm-package
%post
systemctl daemon-reload
systemctl enable plugsched
systemctl start plugsched

#uninstall plugsched module before remove this rpm-package
%preun
systemctl --no-reload disable plugsched
systemctl stop plugsched

%postun
systemctl daemon-reload

%files
%{_bindir}/plugsched
%{_bindir}/plugsched-install
%{_bindir}/plugsched-uninstall
%{_bindir}/plugsched-verses-kpatch.py
%{_prefix}/lib/systemd/system/plugsched.service
%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/plugsched.ko
%{_localstatedir}/plugsched/%{KVER}-%{KREL}.%{_arch}/abi_blacklist
%{_tmpfilesdir}/plugsched.conf
%{_sysconfdir}/yum/plugsched-actions/plugsched.action
%{_sysconfdir}/yum/pluginconf.d/plugsched-actions.conf
/usr/lib/yum-plugins/plugsched-actions.py

%dir
%{_rundir}/plugsched

%changelog
