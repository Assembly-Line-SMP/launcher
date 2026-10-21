import shutil
import sys
from pathlib import Path


source_dir = Path(sys.argv[1])
package_dir = Path(sys.argv[2])
version = sys.argv[3]
gresource = Path(sys.argv[4])
stamp = Path(sys.argv[5])
application_package = package_dir / 'assemblylinelauncher'

if package_dir.exists():
    shutil.rmtree(package_dir)

application_package.mkdir(parents=True, exist_ok=True)
for module in ('__init__.py', 'main.py', 'window.py'):
    shutil.copy2(source_dir / module, application_package / module)

for package in ('pages', 'launcher'):
    shutil.copytree(
        source_dir / package,
        application_package / package,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.code-workspace'),
    )

shutil.copy2(gresource, package_dir / 'assemblylinelauncher.gresource')
(package_dir / 'main.py').write_text(
    """import sys
from pathlib import Path

from gi.repository import Gio

if '__compiled__' in globals():
    application_dir = Path(__compiled__.containing_dir)
else:
    application_dir = Path(__file__).resolve().parent

resource = Gio.Resource.load(str(application_dir / 'assemblylinelauncher.gresource'))
resource._register()

from assemblylinelauncher.main import main

sys.exit(main({version!r}))
""".format(version=version),
    encoding='utf-8',
)
(package_dir / 'README.md').write_text(
    """# Nuitka staging directory

Build a standalone executable from this directory with:

```sh
python3 -m nuitka --standalone --include-package=assemblylinelauncher \\
  --include-data-file=assemblylinelauncher.gresource=assemblylinelauncher.gresource \\
  main.py
```

GTK 4, libadwaita, and their GObject Introspection libraries remain system
runtime dependencies and must be available in the target environment.
""",
    encoding='utf-8',
)

stamp.parent.mkdir(parents=True, exist_ok=True)
stamp.write_text('Nuitka staging directory is ready.\n', encoding='utf-8')