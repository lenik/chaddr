project(
    'chaddr',
    meson_version: '>=1.2.0',
    version: run_command(
        'sh',
        '-c', '''
        v=$(git describe --tags --always --dirty 2>/dev/null || true)
        v=${v#v}
        if [ -n "$v" ]; then
            if [ "${v%.*}" = "$v" ]; then
                v="0.0.0-$v"
            fi
            printf "%s" "$v"
        else
            echo "0.0.0"
        fi
        ''',
        check: false,
    ).stdout().strip(),
    license: 'AGPL-3.0-or-later',
    default_options: ['buildtype=release'],
)

fs = import('fs')

py = import('python').find_installation('python3', pure: false)
py_install_dir = py.get_install_dir()

prefix = get_option('prefix')
datadir = get_option('datadir')
sysconfdir = get_option('sysconfdir')
bindir = get_option('bindir')

profile_dir = get_option('profile_dir')
if profile_dir == ''
    profile_dir = datadir / 'chaddr' / 'profile'
endif

doc_dir = get_option('doc_dir')
if doc_dir == ''
    doc_dir = datadir / 'doc' / 'chaddr' / 'examples'
endif

bashcompletiondir = get_option('bashcompletiondir')
if bashcompletiondir == ''
    bashcompletiondir = datadir / 'bash-completion' / 'completions'
endif

if profile_dir.startswith('/')
    installed_profile_dir = profile_dir
else
    installed_profile_dir = prefix / profile_dir
endif

if doc_dir.startswith('/')
    installed_doc_dir = doc_dir
else
    installed_doc_dir = prefix / doc_dir
endif

chaddr_conf = configuration_data()
chaddr_conf.set('PACKAGE', meson.project_name())
chaddr_conf.set('VERSION', meson.project_version())
chaddr_conf.set('PREFIX', prefix)
chaddr_conf.set('DATADIR', prefix / datadir)
chaddr_conf.set('SYSCONFDIR', prefix / sysconfdir)
chaddr_conf.set('PROFILE_DIR', installed_profile_dir)
chaddr_conf.set('DOC_DIR', installed_doc_dir)

subdir('chaddr')

launcher_conf = configuration_data()
launcher_conf.set('PYTHON', py.full_path())

configure_file(
    input: 'chaddr.py',
    output: 'chaddr.py',
    configuration: launcher_conf,
    install: true,
    install_dir: bindir,
    install_mode: 'rwxr-xr-x',
)

install_data(
    'chaddr.conf.template',
    install_dir: doc_dir,
)

configure_file(
    input: 'man/chaddr.1.in',
    output: 'chaddr.1',
    configuration: chaddr_conf,
    install: true,
    install_dir: get_option('mandir') / 'man1',
)

configure_file(
    input: 'man/chaddr-profile.5.in',
    output: 'chaddr-profile.5',
    configuration: chaddr_conf,
    install: true,
    install_dir: get_option('mandir') / 'man5',
)

install_data(
    'chaddr.bash',
    rename: 'chaddr',
    install_dir: bashcompletiondir,
)

if fs.is_dir('profile')
    install_subdir(
        'profile',
        install_dir: profile_dir,
        strip_directory: true,
    )
endif

summary(
    {
        'prefix': prefix,
        'bindir': prefix / bindir,
        'python package': py_install_dir / 'chaddr',
        'profile_dir': installed_profile_dir,
        'doc_dir': installed_doc_dir,
        'sysconfdir': prefix / sysconfdir,
    },
    section: 'chaddr',
)

# look task: list what installed files looks like
# ninja install <tmpdir> && tree <tmpdir> && rm -fr <tmpdir>
run_target(
    'look',
    command: [
        'bash',
        '-euc', '''
            tmpdir=$(mktemp -d)
            DESTDIR=$tmpdir meson install -C "@BUILD_ROOT@"
            tree "$tmpdir"
            rm -fr "$tmpdir"
        ''',
    ],
)
