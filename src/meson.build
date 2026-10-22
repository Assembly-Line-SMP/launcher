pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'assemblylinelauncher'
gnome = import('gnome')

assemblylinelauncher_gresource = gnome.compile_resources('assemblylinelauncher',
  'assemblylinelauncher.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

python = import('python')
python3 = python.find_installation('python3')

conf = configuration_data()
conf.set('PYTHON', python3.full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'assemblylinelauncher.in',
  output: 'assemblylinelauncher',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x'
)

assemblylinelauncher_sources = [
  '__init__.py',
  'main.py',
  'window.py'
]

install_data(assemblylinelauncher_sources, install_dir: moduledir)

install_subdir('pages', install_dir: moduledir)
install_subdir('launcher', install_dir: moduledir)

