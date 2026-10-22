#!/usr/bin/env python3
"""Bundle the GTK 4 runtime into a Nuitka standalone distribution.

The produced bundles are self-contained: they ship the GTK 4 and libadwaita
shared libraries, the GObject Introspection typelibs, the compiled GSettings
schemas, the icon theme and the gdk-pixbuf loaders, so the application runs on
machines that do not have GTK installed.

Usage:
    bundle_runtime.py windows --dist DIR --output DIR --prefix PREFIX
    bundle_runtime.py macos --dist DIR --output DIR --prefix PREFIX
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Shared libraries that must be present in the bundle. The patterns cover the
# different suffixes used by the distributions (MSYS2 ships ``libgtk-4-1.dll``,
# Homebrew ships ``libgtk-4.dylib``).
ROOT_LIBRARY_PATTERNS = (
    'libgtk-4*',
    'libadwaita-1*',
    'libgirepository-1.0*',
)

# Icon themes that are shipped with the bundle.
ICON_THEMES = ('Adwaita', 'AdwaitaLegacy', 'hicolor', 'default')

# API set stubs are resolved by the operating system loader.
API_SET_PREFIXES = ('api-ms-win-', 'ext-ms-')


class BundleError(Exception):
    """Raised when the runtime cannot be bundled completely."""


def log(message: str) -> None:
    print(f'[bundle] {message}', flush=True)


def copy_file(source: Path, destination: Path) -> None:
    """Copy a file, creating the destination directory if needed."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    # Some of the bundled libraries are read-only; make the copy writable so
    # that the archive step can handle it.
    try:
        destination.chmod(destination.stat().st_mode | 0o200)
    except OSError:
        pass


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command and capture its output."""
    return subprocess.run(command, capture_output=True, text=True, check=check)


# ---------------------------------------------------------------------------
# PE (Windows) support
# ---------------------------------------------------------------------------


class PEError(Exception):
    """Raised when a file cannot be parsed as a PE image."""


class PEImage:
    """Minimal PE reader used to extract imported library names."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = path.read_bytes()
        self._parse_headers()

    def _u16(self, offset: int) -> int:
        return int.from_bytes(self.data[offset:offset + 2], 'little')

    def _u32(self, offset: int) -> int:
        return int.from_bytes(self.data[offset:offset + 4], 'little')

    def _parse_headers(self) -> None:
        data = self.data
        if data[:2] != b'MZ':
            raise PEError(f'{self.path} is not a PE image')
        pe_offset = self._u32(0x3C)
        if data[pe_offset:pe_offset + 4] != b'PE\0\0':
            raise PEError(f'{self.path} has no PE signature')
        coff = pe_offset + 4
        section_count = self._u16(coff + 2)
        optional_size = self._u16(coff + 16)
        optional = coff + 20
        magic = self._u16(optional)
        if magic == 0x10B:
            directories = optional + 96
        elif magic == 0x20B:
            directories = optional + 112
        else:
            raise PEError(f'{self.path} has an unknown optional header')
        directory_count = self._u32(directories - 4)
        self.sections: list[tuple[int, int, int, int]] = []
        section_table = optional + optional_size
        for index in range(section_count):
            entry = section_table + index * 40
            virtual_size = self._u32(entry + 8)
            virtual_address = self._u32(entry + 12)
            raw_size = self._u32(entry + 16)
            raw_pointer = self._u32(entry + 20)
            self.sections.append(
                (virtual_address, max(virtual_size, raw_size), raw_pointer, raw_size)
            )
        self.import_directory = self._directory(directories, 1, directory_count)
        self.delay_directory = self._directory(directories, 13, directory_count)

    def _directory(self, directories: int, index: int, count: int) -> tuple[int, int]:
        if index >= count:
            return (0, 0)
        offset = directories + index * 8
        return (self._u32(offset), self._u32(offset + 4))

    def _rva_to_offset(self, rva: int) -> int:
        for virtual_address, size, raw_pointer, raw_size in self.sections:
            if virtual_address <= rva < virtual_address + size:
                return raw_pointer + (rva - virtual_address)
        raise PEError(f'{self.path} maps no section for RVA {rva:#x}')

    def _string_at(self, rva: int) -> str:
        offset = self._rva_to_offset(rva)
        end = self.data.index(b'\0', offset)
        return self.data[offset:end].decode('ascii', 'replace')

    def imports(self) -> list[str]:
        """Return the names of the libraries imported by this image."""
        names: list[str] = []
        if self.import_directory[0]:
            offset = self._rva_to_offset(self.import_directory[0])
            while True:
                name_rva = self._u32(offset + 12)
                if name_rva == 0:
                    break
                names.append(self._string_at(name_rva))
                offset += 20
        if self.delay_directory[0]:
            offset = self._rva_to_offset(self.delay_directory[0])
            while True:
                name_rva = self._u32(offset + 4)
                if name_rva == 0:
                    break
                names.append(self._string_at(name_rva))
                offset += 32
        return names


def is_pe(path: Path) -> bool:
    """Return True when the file looks like a PE image."""
    try:
        with path.open('rb') as handle:
            return handle.read(2) == b'MZ'
    except OSError:
        return False


def is_api_set(name: str) -> bool:
    """Return True for API set stub libraries provided by the system."""
    return name.lower().startswith(API_SET_PREFIXES)


def ntldd_dependencies(binary: Path) -> dict[str, Path] | None:
    """Resolve the dependencies of a PE file with ``ntldd``.

    Returns a mapping of library name to the file it resolves to, or ``None``
    when ``ntldd`` is not available.
    """
    if shutil.which('ntldd') is None:
        return None
    result = run(['ntldd', str(binary)], check=False)
    if result.returncode != 0:
        return None
    resolved: dict[str, Path] = {}
    for line in result.stdout.splitlines():
        if '=>' not in line:
            continue
        name, _, target = line.partition('=>')
        name = name.strip()
        target = target.strip().split(' (')[0].strip()
        if not name or not target:
            continue
        resolved[name] = Path(target)
    return resolved


def windows_system_directories() -> list[Path]:
    """Return the directories that hold the operating system libraries."""
    candidates = [
        Path('C:/Windows/System32'),
        Path('/c/Windows/System32'),
        Path('/mingw64/Windows/System32'),
    ]
    return [candidate for candidate in candidates if candidate.is_dir()]


def is_windows_system_library(name: str) -> bool:
    """Return True when the library is provided by the operating system."""
    if is_api_set(name):
        return True
    for directory in windows_system_directories():
        if (directory / name).is_file():
            return True
    return False


def is_windows_system_path(path: Path) -> bool:
    """Return True when the path points into the operating system."""
    text = str(path).replace('\\', '/').lower()
    return 'windows/system32' in text or 'windows/syswow64' in text


def resolve_windows_library(
    name: str, search_paths: list[Path]
) -> Path | None:
    """Find a library by file name inside the given search paths."""
    if is_windows_system_library(name):
        return None
    for directory in search_paths:
        candidate = directory / name
        if candidate.is_file():
            return candidate
        stem = name[:-4] if name.lower().endswith('.dll') else name
        for entry in sorted(directory.glob(f'{stem}*.dll')):
            if entry.is_file():
                return entry
    return None


def collect_windows_libraries(
    destination: Path,
    search_paths: list[Path],
    roots: list[Path],
) -> tuple[dict[Path, str], list[str]]:
    """Copy the transitive library closure of the roots into the bundle.

    The import table of every image is the source of truth. ``ntldd`` is used
    to turn an imported name into a file, and the PE reader is the fallback
    when that tool is unavailable.

    Returns the copied libraries and the imports that could not be resolved.
    """
    copied: dict[Path, str] = {}
    unresolved: set[str] = set()
    queue: list[Path] = [root for root in roots if root.is_file()]
    seen: set[str] = set()
    while queue:
        library = queue.pop()
        key = library.name.lower()
        if key in seen:
            continue
        seen.add(key)
        target = destination / library.name
        if not target.exists() and not is_inside(library, destination):
            # Only the libraries that are not part of the distribution are
            # copied; the Python modules keep their place in the tree.
            copy_file(library, target)
            copied[library] = library.name
        if not is_pe(library):
            continue

        try:
            image = PEImage(library)
        except PEError as error:
            log(f'warning: {error}')
            continue

        resolved_by_ntldd = ntldd_dependencies(library) or {}
        for imported in image.imports():
            if is_windows_system_library(imported):
                continue
            resolved = resolved_by_ntldd.get(imported)
            if resolved is not None and resolved.is_file():
                if is_windows_system_path(resolved):
                    continue
                queue.append(resolved)
                continue
            fallback = resolve_windows_library(imported, search_paths)
            if fallback is None:
                if not is_provided_by_the_environment(imported):
                    unresolved.add(imported)
                continue
            queue.append(fallback)
    return copied, sorted(unresolved)


def is_provided_by_the_environment(name: str) -> bool:
    """Return True for libraries that the runtime environment provides.

    The MSYS2 environment ships its own runtime libraries in the MSYS tree.
    They are not needed by the bundle because the MSYS2 runtime is only used
    to build it.
    """
    lowered = name.lower()
    return lowered.startswith(('msys-', 'cyg')) or lowered == 'cygwin1.dll'


# ---------------------------------------------------------------------------
# Mach-O (macOS) support
# ---------------------------------------------------------------------------


def is_macho(path: Path) -> bool:
    """Return True when the file looks like a Mach-O image."""
    try:
        with path.open('rb') as handle:
            magic = handle.read(4)
    except OSError:
        return False
    return magic in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe')


def macho_dependencies(path: Path) -> list[str]:
    """Return the libraries linked by a Mach-O image."""
    result = run(['otool', '-L', str(path)])
    dependencies: list[str] = []
    for line in result.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        dependencies.append(line.split(' (')[0].strip())
    return dependencies


def macho_rpaths(path: Path) -> list[str]:
    """Return the runtime search paths declared by a Mach-O image."""
    result = run(['otool', '-l', str(path)])
    rpaths: list[str] = []
    lines = result.stdout.splitlines()
    for index, line in enumerate(lines):
        if 'cmd LC_RPATH' in line:
            for follow in lines[index + 1:index + 4]:
                if 'path ' in follow:
                    rpaths.append(follow.split('path ')[1].split(' (')[0].strip())
                    break
    return rpaths


def is_system_library(path: str) -> bool:
    """Return True for libraries provided by the operating system."""
    return path.startswith('/usr/lib/') or path.startswith('/System/')


def resolve_macos_library(
    dependency: str, loader: Path, search_paths: list[Path]
) -> Path | None:
    """Resolve a Mach-O dependency reference to a file on disk."""
    if dependency.startswith('@loader_path/'):
        candidate = loader.parent / dependency[len('@loader_path/'):]
        return candidate if candidate.is_file() else None
    if dependency.startswith('@rpath/'):
        name = dependency[len('@rpath/'):]
        for rpath in macho_rpaths(loader):
            if rpath.startswith('@loader_path/'):
                rpath = str(loader.parent / rpath[len('@loader_path/'):])
            candidate = Path(rpath) / name
            if candidate.is_file():
                return candidate
        for directory in search_paths:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        return None
    if dependency.startswith('@executable_path/'):
        return None
    candidate = Path(dependency)
    return candidate if candidate.is_file() else None


def rewrite_macho_paths(
    image: Path, changes: dict[str, str], identifier: str | None = None
) -> None:
    """Point the dependencies of a Mach-O image at the bundled copies."""
    if identifier:
        run(['install_name_tool', '-id', identifier, str(image)])
    for old, new in changes.items():
        run(['install_name_tool', '-change', old, new, str(image)])


def resign_macos_bundle(out: Path) -> None:
    """Re-sign the bundle after the Mach-O files were modified.

    Rewriting a Mach-O image invalidates its signature, and arm64 macOS
    refuses to load an image without a valid one. The bundle is signed with an
    ad-hoc signature, which is enough to run it locally.
    """
    if shutil.which('codesign') is None:
        log('warning: codesign is not available')
        return
    result = run(
        [
            'codesign',
            '--force',
            '--deep',
            '--sign',
            '-',
            str(out),
        ],
        check=False,
    )
    if result.returncode != 0:
        log(f'warning: codesign failed: {result.stderr.strip()}')
        return
    log('bundle signed with an ad-hoc signature')


def collect_macos_libraries(
    destination: Path,
    search_paths: list[Path],
    roots: list[Path],
) -> dict[Path, str]:
    """Copy the transitive library closure of the roots into the bundle."""
    copied: dict[Path, str] = {}
    queue: list[Path] = list(roots)
    seen: set[str] = set()
    while queue:
        library = queue.pop()
        key = str(library)
        if key in seen:
            continue
        seen.add(key)
        target = destination / library.name
        if not target.exists() and not is_inside(library, destination):
            # Only the libraries that are not part of the distribution are
            # copied; the Python modules keep their place in the tree.
            copy_file(library, target)
            copied[library] = library.name
        if not is_macho(library):
            continue
        changes: dict[str, str] = {}
        for dependency in macho_dependencies(library):
            if is_system_library(dependency):
                continue
            resolved = resolve_macos_library(dependency, library, search_paths)
            if resolved is None:
                log(f'note: leaving {dependency} to the system')
                continue
            changes[dependency] = f'@loader_path/{resolved.name}'
            queue.append(resolved)
        if not changes:
            continue
        if is_inside(library, destination):
            # The library is already part of the bundle.
            rewrite_macho_paths(library, changes)
        else:
            rewrite_macho_paths(
                target, changes, identifier=f'@loader_path/{library.name}'
            )
    return copied


# ---------------------------------------------------------------------------
# Shared runtime data
# ---------------------------------------------------------------------------


def find_directory(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def is_inside(path: Path, directory: Path) -> bool:
    """Return True when the path is inside the given directory."""
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def copy_gi_assets(
    out: Path,
    prefix: Path,
    stage: Path | None,
    extra_prefixes: list[Path],
) -> str | None:
    """Copy typelibs, schemas, icons and pixbuf loaders into the bundle.

    The assets are looked up in the installation prefix and in the extra
    prefixes, which covers the different layouts of MSYS2 and Homebrew.
    """
    prefixes = [prefix] + extra_prefixes

    typelib_dirs = [
        base / 'lib' / 'girepository-1.0' for base in prefixes
    ] + [
        base / 'share' / 'girepository-1.0' for base in prefixes
    ]
    typelib_source = find_directory(typelib_dirs)
    if typelib_source is None:
        raise BundleError('no GObject Introspection typelibs found')
    log(f'typelibs from {typelib_source}')
    typelib_target = out / 'lib' / 'girepository-1.0'
    typelib_target.mkdir(parents=True, exist_ok=True)
    for typelib in sorted(typelib_source.glob('*.typelib')):
        copy_file(typelib, typelib_target / typelib.name)

    schema_dir = out / 'share' / 'glib-2.0' / 'schemas'
    schema_dir.mkdir(parents=True, exist_ok=True)
    schema_source = find_directory(
        [base / 'share' / 'glib-2.0' / 'schemas' for base in prefixes]
    )
    if schema_source is not None:
        log(f'schemas from {schema_source}')
        for schema in sorted(schema_source.glob('*.xml')):
            copy_file(schema, schema_dir / schema.name)
    if stage is not None:
        staged_schemas = stage / 'share' / 'glib-2.0' / 'schemas'
        if staged_schemas.is_dir():
            for schema in sorted(staged_schemas.glob('*.xml')):
                copy_file(schema, schema_dir / schema.name)

    icon_source = find_directory(
        [base / 'share' / 'icons' for base in prefixes]
    )
    if icon_source is not None:
        log(f'icons from {icon_source}')
        for theme in ICON_THEMES:
            source = icon_source / theme
            if source.is_dir():
                shutil.copytree(
                    source, out / 'share' / 'icons' / theme, dirs_exist_ok=True
                )
    if stage is not None:
        staged_icons = stage / 'share' / 'icons'
        if staged_icons.is_dir():
            shutil.copytree(staged_icons, out / 'share' / 'icons', dirs_exist_ok=True)

    loader_source = find_directory(
        [base / 'lib' / 'gdk-pixbuf-2.0' for base in prefixes]
    )
    if loader_source is None:
        log('warning: no gdk-pixbuf loaders found')
        return None
    versions = sorted(entry for entry in loader_source.iterdir() if entry.is_dir())
    if not versions:
        log('warning: no gdk-pixbuf loader version found')
        return None
    loader_version = versions[-1]
    log(f'pixbuf loaders from {loader_version}')
    shutil.copytree(
        loader_version,
        out / 'lib' / 'gdk-pixbuf-2.0' / loader_version.name,
        dirs_exist_ok=True,
    )
    return loader_version.name


def rewrite_pixbuf_cache(out: Path, loader_version: str | None) -> None:
    """Make the pixbuf loader cache use paths inside the bundle."""
    if not loader_version:
        return
    cache = out / 'lib' / 'gdk-pixbuf-2.0' / loader_version / 'loaders.cache'
    if not cache.is_file():
        return
    prefix = f'lib/gdk-pixbuf-2.0/{loader_version}'
    lines: list[str] = []
    for line in cache.read_text(encoding='utf-8', errors='replace').splitlines():
        stripped = line.strip()
        if stripped.startswith('# LoaderDir = '):
            # The header records where the loader modules live.
            line = f'# LoaderDir = {prefix}/loaders'
        elif stripped.startswith('"') and stripped.endswith('.so"'):
            # A module line holds the path of the loader library. The path is
            # relative to the bundle root; the launcher script changes into
            # that directory before starting the application.
            closing = stripped.find('"', 1)
            relative = Path(stripped[1:closing]).name
            if 'loaders' in Path(stripped[1:closing]).parts:
                relative = f'loaders/{relative}'
            line = f'"{prefix}/{relative}"'
        lines.append(line)
    cache.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def compile_schemas(out: Path) -> None:
    """Compile the bundled GSettings schemas."""
    schema_dir = out / 'share' / 'glib-2.0' / 'schemas'
    if not schema_dir.is_dir():
        return
    if shutil.which('glib-compile-schemas') is None:
        log('warning: glib-compile-schemas is not available')
        return
    result = run(['glib-compile-schemas', str(schema_dir)], check=False)
    if result.returncode != 0:
        log(f'warning: glib-compile-schemas failed: {result.stderr.strip()}')
        return
    log('GSettings schemas compiled')


def write_windows_launcher(out: Path, binary: str, loader_version: str | None) -> None:
    loader_dir = (
        f'%BUNDLE_ROOT%\\lib\\gdk-pixbuf-2.0\\{loader_version}' if loader_version else ''
    )
    script = f'''@echo off
setlocal
set "BUNDLE_ROOT=%~dp0"
cd /d "%BUNDLE_ROOT%"
set "GI_TYPELIB_PATH=%BUNDLE_ROOT%lib\\girepository-1.0"
set "GSETTINGS_SCHEMA_DIR=%BUNDLE_ROOT%share\\glib-2.0\\schemas"
set "XDG_DATA_DIRS=%BUNDLE_ROOT%share"
set "GDK_PIXBUF_MODULEDIR={loader_dir}"
set "GDK_PIXBUF_MODULE_FILE={loader_dir}\\loaders.cache"
set "PATH=%BUNDLE_ROOT%;{loader_dir};%PATH%"
"%BUNDLE_ROOT%{binary}" %*
'''
    launcher = out / 'AssemblyLineLauncher.bat'
    launcher.write_text(script, encoding='utf-8')


def copy_readme(out: Path, readme: Path | None) -> None:
    """Copy the instructions for the bundle next to the launcher."""
    if readme is None or not readme.is_file():
        return
    copy_file(readme, out / readme.name)


def write_macos_launcher(out: Path, binary: str, loader_version: str | None) -> None:
    loader_dir = (
        f'"$BUNDLE_ROOT/lib/gdk-pixbuf-2.0/{loader_version}"' if loader_version else '""'
    )
    script = f'''#!/bin/sh
BUNDLE_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$BUNDLE_ROOT" || exit 1
export GI_TYPELIB_PATH="$BUNDLE_ROOT/lib/girepository-1.0"
export GSETTINGS_SCHEMA_DIR="$BUNDLE_ROOT/share/glib-2.0/schemas"
export XDG_DATA_DIRS="$BUNDLE_ROOT/share:$XDG_DATA_DIRS"
export GDK_PIXBUF_MODULEDIR={loader_dir}
export GDK_PIXBUF_MODULE_FILE="$GDK_PIXBUF_MODULEDIR/loaders.cache"
export DYLD_LIBRARY_PATH="$BUNDLE_ROOT:$DYLD_LIBRARY_PATH"
exec "$BUNDLE_ROOT/{binary}" "$@"
'''
    launcher = out / 'AssemblyLineLauncher.command'
    launcher.write_text(script, encoding='utf-8')
    launcher.chmod(0o755)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def bundle_windows(args: argparse.Namespace) -> None:
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.dist, out, dirs_exist_ok=True)
    prefix = Path(args.prefix)
    search_paths = [prefix / 'bin', prefix / 'lib', out]
    # MSYS2 keeps the runtime libraries in the bin directory of the prefix.
    for extra in ('/mingw64/bin', '/usr/bin'):
        candidate = Path(extra)
        if candidate.is_dir():
            search_paths.append(candidate)

    roots: list[Path] = []
    for pattern in ROOT_LIBRARY_PATTERNS:
        for directory in (prefix / 'bin', prefix / 'lib'):
            for candidate in sorted(directory.glob(f'{pattern}.dll')):
                roots.append(candidate)
    if not roots:
        raise BundleError(f'no GTK libraries found in {prefix / "bin"}')

    # The distribution holds the Python modules and the extension modules;
    # the latter import the GTK libraries and are part of the closure.
    for image in sorted(out.rglob('*')):
        if image.is_file() and is_pe(image):
            roots.append(image)

    log(f'collecting the library closure for {len(roots)} roots')
    copied, unresolved = collect_windows_libraries(out, search_paths, roots)
    log(f'copied {len(copied)} libraries')
    if unresolved:
        log(f'warning: unresolved imports: {", ".join(unresolved)}')
        if args.strict:
            raise BundleError(
                f'unresolved imports are not bundled: {", ".join(unresolved)}'
            )

    loader_version = copy_gi_assets(out, prefix, args.stage, [])
    rewrite_pixbuf_cache(out, loader_version)
    compile_schemas(out)
    write_windows_launcher(out, args.binary, loader_version)
    copy_readme(out, args.readme)


def bundle_macos(args: argparse.Namespace) -> None:
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.dist, out, dirs_exist_ok=True)
    prefix = Path(args.prefix)
    search_paths = [prefix / 'lib', prefix / 'bin']
    search_paths.extend(sorted((prefix / 'opt').glob('*/lib')))
    # The assets live in the Homebrew prefix, not in the library directories.
    asset_prefixes = [prefix]
    asset_prefixes.extend(sorted((prefix / 'opt').glob('*')))

    roots: list[Path] = []
    seen_real: set[Path] = set()
    for pattern in ROOT_LIBRARY_PATTERNS:
        for directory in search_paths:
            for candidate in sorted(directory.glob(f'{pattern}.dylib')):
                real = candidate.resolve()
                if real in seen_real:
                    continue
                seen_real.add(real)
                roots.append(candidate)
    if not roots:
        raise BundleError(f'no GTK libraries found in {prefix}')

    log(f'collecting the library closure for {len(roots)} roots')
    copied = collect_macos_libraries(out, search_paths, roots)
    log(f'copied {len(copied)} libraries')

    # The Nuitka distribution holds the executable and the extension modules
    # in subdirectories, so they are pointed at the bundled copies with
    # @executable_path, which is the root of the bundle.
    bundled = {name.lower() for name in copied.values()}
    for image in sorted(out.rglob('*')):
        if not image.is_file() or not is_macho(image):
            continue
        changes: dict[str, str] = {}
        for dependency in macho_dependencies(image):
            if is_system_library(dependency):
                continue
            resolved = resolve_macos_library(dependency, image, search_paths)
            if resolved is not None and resolved.name.lower() in bundled:
                changes[dependency] = f'@executable_path/{resolved.name}'
        if changes:
            rewrite_macho_paths(image, changes)

    loader_version = copy_gi_assets(out, prefix, args.stage, asset_prefixes)
    rewrite_pixbuf_cache(out, loader_version)
    compile_schemas(out)
    write_macos_launcher(out, args.binary, loader_version)
    resign_macos_bundle(out)
    copy_readme(out, args.readme)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform', choices=('windows', 'macos'))
    parser.add_argument('--dist', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--stage', type=Path, default=None)
    parser.add_argument('--binary', default='main.exe')
    parser.add_argument('--readme', type=Path, default=None)
    parser.add_argument('--strict', action='store_true')
    args = parser.parse_args()

    try:
        if args.platform == 'windows':
            bundle_windows(args)
        else:
            bundle_macos(args)
    except BundleError as error:
        log(f'error: {error}')
        return 1
    log(f'bundle written to {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
