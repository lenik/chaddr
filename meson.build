project(
    'chaddr',
    version: run_command(
        'sh',
        '-c', '''
        v=$(
            command -v zfr >/dev/null 2>&1 && zfr version 2>/dev/null || true
        )
        if [ -z "$v" ] && [ -f VERSION ]; then
            v=$(head -n1 VERSION | tr -d '\r')
            v=${v#v}
        fi
        if [ -z "$v" ]; then
            v="0.0.0" # FIXED TO 0.0.0, DO NOT MODIFY
        fi
        printf '%s' "$v"
        ''',
        check: false,
    ).stdout().strip(),
    license: 'AGPL-3.0-or-later',
    default_options: ['buildtype=release'],
)
project_author = 'Lenik (谢继雷)'
project_email = 'lenik@bodz.net'
project_year = 2026


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
chaddr_conf.set('PYTHON', '/usr/bin/python3')
chaddr_conf.set('PY_INSTALL_DIR', py_install_dir)

subdir('chaddr')

if fs.is_dir('po')
    subdir('po')
endif

if fs.is_dir('tests')
    test(
        'python-unit',
        py,
        args: [
            '-m',
            'unittest',
            'discover',
            '-s',
            (meson.project_source_root() / 'tests'),
            '-p',
            'test_*.py',
        ],
        env: [
            'PYTHONPATH=' + meson.project_source_root(),
            'CHADDR_LOCALEDIR=' + (meson.project_build_root() / 'po'),
        ],
    )
endif

run_target(
    'posync',
    command: [
        'bash',
        '-euc', '''
            cd "@SOURCE_ROOT@/po"
            xgettext --from-code=UTF-8 --keyword=_ --language=Python --directory=.. --output=chaddr.pot --files-from=POTFILES
            while IFS= read -r lang; do
                [ -n "$lang" ] || continue
                case "$lang" in
                    '#'* ) continue ;;
                esac
                po_file="$lang.po"
                if [ ! -f "$po_file" ]; then
                    msginit --no-translator --input=chaddr.pot --locale="$lang" --output-file="$po_file"
                fi
                msgmerge --update --backup=none "$po_file" chaddr.pot
                msgattrib --no-obsolete --output-file="$po_file" "$po_file"
            done < LINGUAS
        ''',
    ],
)

chaddr_launcher = configure_file(
    input: 'chaddr.py.in',
    output: 'chaddr-launcher.py',
    configuration: chaddr_conf,
)

install_data(
    chaddr_launcher,
    install_dir: bindir,
    install_mode: 'rwxr-xr-x',
    rename: 'chaddr',
)

install_data(
    'chaddr.conf.template',
    install_dir: doc_dir,
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

asciidoctor = find_program('asciidoctor', required: true)

mandir = prefix / get_option('mandir')
pkgdocdir = datadir / 'doc' / meson.project_name()

install_data(
    [
        'LICENSE',
        'README.md',
        'README-zh.md',
    ],
    install_dir: pkgdocdir,
)

puffs = [
    'chaddr',
    'chaddr-profile',
]
man_i18n_langs = []
foreach lang : ['ar', 'de', 'es_MX', 'fr', 'it', 'ja', 'ko', 'zh_CN']
  if fs.is_dir('docs' / lang)
    man_i18n_langs += [lang]
  endif
endforeach

man_i18n_built = []
foreach puff : puffs
  custom_target(
    puff + '-man',
    input: 'docs' / (puff + '.adoc'),
    output: puff + '.1',
    command: [
      asciidoctor,
      '-b', 'manpage',
      '-a', 'project-version=' + meson.project_version(),
      '-a', 'project-year=@0@'.format(project_year),
      '-a', 'project-author=' + project_author,
      '-a', 'project-email=' + project_email,
      '-o', '@OUTPUT@',
      '@INPUT@',
    ],
    build_by_default: true,
    install: true,
    install_dir: mandir / 'man1',
  )
  foreach lang : man_i18n_langs
    man_i18n_built += custom_target(
      puff + '-' + lang + '-man',
      input: 'docs' / lang / (puff + '.adoc'),
      output: puff + '--' + lang + '.1',
      command: [
        asciidoctor,
        '-b', 'manpage',
        '-a', 'project-version=' + meson.project_version(),
        '-a', 'project-year=@0@'.format(project_year),
        '-a', 'project-author=' + project_author,
        '-a', 'project-email=' + project_email,
        '-o', '@OUTPUT@',
        '@INPUT@',
      ],
      build_by_default: true,
    )
  endforeach
endforeach

meson.add_install_script(
  find_program('python3'),
  '-c',
  '''
import os, sys, pathlib, shutil
build = pathlib.Path(sys.argv[1])
mandir = sys.argv[2]
dest = os.environ.get("MESON_INSTALL_DESTDIR_PREFIX") or (
    os.environ.get("DESTDIR", "") + os.environ.get("MESON_INSTALL_PREFIX", "")
)
for name in sys.argv[3:]:
    # chaddr--zh_CN.1 or chaddr-profile--es_MX.1
    if "--" not in name or not name.endswith(".1"):
        raise SystemExit("unexpected man artifact: " + name)
    stem, lang = name[:-2].rsplit("--", 1)
    src = build / name
    if not src.is_file():
        raise SystemExit("missing built man page: " + str(src))
    outdir = pathlib.Path(dest) / mandir / lang / "man1"
    outdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, outdir / (stem + ".1"))
''',
  meson.project_build_root(),
  get_option('mandir'),
  man_i18n_built,
)
