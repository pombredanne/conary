Summary: Pre-usrmove, has a file in /sbin
Name: usrmove
Version: 1.0
Release: 1
ExclusiveOs: Linux
BuildRoot: %{_tmppath}/%{name}-root
Group: something
License: something

%description
junk

%prep

%build

%install
mkdir -p $RPM_BUILD_ROOT/sbin
echo 1.0 > $RPM_BUILD_ROOT/sbin/usrmove

%clean
rm -rf $RPM_BUILD_ROOT

%files
%attr(-,root,root) /sbin/usrmove
