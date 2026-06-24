chaddr_py_sources = files(
  '__init__.py',
  '__main__.py',
  '_version.py',
  'address.py',
  'cli.py',
  'config.py',
  'logutil.py',
  'namecheap_portal.py',
  'orchestrator.py',
  'privilege.py',
  'profile.py',
  'profile_lexer.py',
  'proxy.py',
  'public_ip.py',
  'gui/__init__.py',
  'gui/address_panel.py',
  'gui/app.py',
  'gui/diagnostics_format.py',
  'gui/highlighter.py',
  'gui/theme.py',
  'types/__init__.py',
  'types/aliyun_elastic_ip.py',
  'types/aws_elastic_ip.py',
  'types/base.py',
  'types/bind_db.py',
  'types/hosts_file.py',
  'types/registered_nameserver.py',
)

configure_file(
  input: 'buildconfig.py.in',
  output: 'buildconfig.py',
  configuration: chaddr_conf,
  install: true,
  install_dir: py_install_dir / 'chaddr',
)

py.install_sources(
  chaddr_py_sources,
  subdir: 'chaddr',
  preserve_path: true,
  pure: false,
)
