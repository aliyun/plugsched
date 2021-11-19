%define dist .al7

Name:		plugsched
Version:	4.19.91
Release:	22.fc.al7.x86_64
Summary:	The plugsched rpm

Group:		System Environment/Kernel
License:	GPLv2
URL:		None
Source0:	None

#Dependent static libraries of symbol_resolve tool
BuildRequires:	elfutils-devel-static > 0.169
BuildRequires:	zlib-static
BuildRequires:	glibc-static

%description
The plugsched rpm-package.

%prep
mkdir -p %{_builddir}/plugsched
cp %{_outdir}/plugsched-install %{_builddir}/plugsched
cp %{_outdir}/plugsched-uninstall %{_builddir}/plugsched
cp %{_outdir}/plugsched.service %{_builddir}/plugsched

%build
#build symbol resolve tool
cd %{_dependdir}/tools/symbol_resolve
make srctree=%{_kerneldir}
cd %{_kerneldir}
LOCALVERSION=-%{RELEASE}.%{_arch} make -f Makefile.plugsched plugsched -j %{threads}

#copy these two files to rpmbuild/BUILD/plugsched
cp %{_dependdir}/tools/symbol_resolve/symbol_resolve %{_builddir}/plugsched
cp %{_kerneldir}/kernel/sched/mod/plugsched.ko %{_builddir}/plugsched

%install
cd plugsched

#install the plugsched tool and plugsched-install script
mkdir -p %{buildroot}/usr/local/bin
mkdir -p %{buildroot}/var/plugsched
install -m 755 symbol_resolve %{buildroot}/usr/local/bin/plugsched
install -m 755 plugsched-install %{buildroot}/usr/local/bin/plugsched-install
install -m 755 plugsched-uninstall %{buildroot}/usr/local/bin/plugsched-uninstall

mkdir -p %{buildroot}/usr/local/share/plugsched
cp plugsched.ko %{buildroot}/usr/local/share/plugsched

#create systemd service
mkdir -p %{buildroot}/usr/lib/systemd/system
cp plugsched.service %{buildroot}/usr/lib/systemd/system

#Auto start the service when system reboot
mkdir -p %{buildroot}/etc/systemd/system/multi-user.target.wants
ln -s  /usr/lib/systemd/system/plugsched.service \
	%{buildroot}/etc/systemd/system/multi-user.target.wants/plugsched.service

#install plugsched module after install this rpm-package
%post
systemctl daemon-reload
systemctl start plugsched

#uninstall plugscehd module before remove this rpm-package
%preun
systemctl stop plugsched
if [ -f "/var/plugsched/plugsched-install-not" ]; then
	/usr/bin/rm -f /var/plugsched/plugsched-install-not
fi

%files
#%doc
/usr/local/bin/plugsched
/usr/local/bin/plugsched-install
/usr/local/bin/plugsched-uninstall
/etc/systemd/system/multi-user.target.wants/plugsched.service
/usr/lib/systemd/system/plugsched.service

%dir
/usr/local/share/plugsched
/var/plugsched

