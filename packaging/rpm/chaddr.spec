# Version is injected by packaging/rpm/Makefile via `zfr version`.
# RPM Version cannot contain '-'; use `zfr version -r` (hyphens → '_').
# srcversion is the unsanitized Meson/git version and names the tarball.
%{!?version:%global version 0.0.0}
%{!?srcversion:%global srcversion %{version}}

Name:           chaddr
Version:        %{version}
Release:        1%{?dist}
Summary:        change or reallocate profile-managed IP addresses

License:        AGPL-3.0-or-later
URL:            https://github.com/lenik/chaddr
Packager:       Lenik (谢继雷) <lenik@bodz.net>
Source0:        %{name}-%{srcversion}.tar.xz

%global debug_package %{nil}
BuildArch:      noarch
BuildRequires:  meson
BuildRequires:  ninja-build
BuildRequires:  python3-all
BuildRequires:  bash-completion
BuildRequires:  asciidoctor
Requires:       python3
Requires:       python3-boto3
Requires:       python3-paramiko
Requires:       python3-requests
Requires:       python3-socks
Requires:       python3-wxgtk4.0
Requires:       policykit-1

%description
chaddr coordinates updates across AWS/Aliyun Elastic IP, Namecheap
registered nameservers, hosts files, BIND zone databases, and OpenWrt
router Shadowsocks remote servers from declarative profile files.
It provides a wxWidgets GUI and a scriptable CLI.
.
Protected file updates from the GUI may prompt for elevation through
pkexec, gksudo, or kdesudo.

%prep
%setup -q -n %{name}-%{srcversion}

%build
meson setup build \
    --prefix=%{_prefix} \
    --bindir=%{_bindir} \
    --datadir=%{_datadir} \
    --mandir=%{_mandir} \
    --sysconfdir=%{_sysconfdir} \
    --localstatedir=%{_localstatedir} \
    --buildtype=plain
meson compile -C build

%install
meson install -C build --destdir=%{buildroot}

%files
%{_bindir}/chaddr
%{_mandir}/man1/chaddr.1*
%{_mandir}/man1/chaddr-profile.1*
%{_datadir}/chaddr/
%{_datadir}/locale/*/LC_MESSAGES/chaddr.mo
%{_mandir}/*/man1/chaddr.1*
%{_mandir}/*/man1/chaddr-profile.1*
%{_datadir}/doc/chaddr/
%changelog
* Thu Aug 20 2026 Lenik (谢继雷) <lenik@bodz.net>
- Align spec with debian/control (Meson, AGPL-3.0-or-later).
- Version comes from `zfr version`, the same method meson.build uses.
