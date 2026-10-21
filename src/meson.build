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

nuitka_package_dir = meson.current_build_dir() / 'nuitka-package'
custom_target('nuitka-package',
  depend_files: files(
    'prepare_nuitka.py',
    '__init__.py',
    'main.py',
    'window.py',
    'pages/__init__.py',
    'pages/assembly_line.py',
    'pages/base.py',
    'pages/explore.py',
    'pages/fix.py',
    'pages/home.py',
    'pages/launch_test.py',
    'launcher/__init__.py',
    'launcher/app.py',
    'launcher/constants.py',
    'launcher/auth/__init__.py',
    'launcher/auth/cracked.py',
    'launcher/auth/manager.py',
    'launcher/auth/microsoft.py',
    'launcher/auth/models.py',
    'launcher/auth/store.py',
    'launcher/core/__init__.py',
    'launcher/core/downloader.py',
    'launcher/core/instance.py',
    'launcher/core/java_manager.py',
    'launcher/core/launch.py',
    'launcher/core/loader_installer.py',
    'launcher/core/modrinth.py',
    'launcher/core/mrpack.py',
    'launcher/core/settings.py',
    'launcher/core/sync.py',
    'launcher/core/vanilla_installer.py',
    'launcher/ui/__init__.py',
    'launcher/ui/account_dialog.py',
    'launcher/ui/main_window.py',
    'launcher/ui/settings_dialog.py',
    'launcher/ui/workers.py',
    'launcher/util/__init__.py',
    'launcher/util/logging_setup.py',
    'launcher/util/paths.py',
  ),
  input: assemblylinelauncher_gresource,
  output: 'nuitka-package.stamp',
  command: [
    python3,
    files('prepare_nuitka.py'),
    meson.current_source_dir(),
    nuitka_package_dir,
    meson.project_version(),
    '@INPUT@',
    '@OUTPUT@',
  ],
  build_by_default: true,
)
