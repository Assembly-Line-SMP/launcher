#!/bin/bash

rm -rf build/AppDir

set -e

#mkdir -p build/AppDir/usr/bin build/AppDir/usr/lib build/AppDir/usr/include build/AppDir/usr/share

mkdir -p build/AppDir

python3 -m venv build/.venv

source build/.venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Copy python
#cp $(readlink -f $(which python3)) build/AppDir/usr/bin/python
#cp -r build/.venv/lib build/AppDir/usr/lib
#cp -r build/.venv/include build/AppDir/usr/include
#cp -r build/.venv/share build/AppDir/usr/share

meson setup --prefix $PWD/build/AppDir/usr build/builddir
meson compile -C build/builddir
meson install -C build/builddir

export ARCH=$(uname -m)

# Check if pacman exists
if command -v pacman &> /dev/null
then
    # Damn guys at appimage-builder wont remove a single letter to make pacman not require root
    sudo appimage-builder --appdir build/AppDir --build-dir build/appimage-build --recipe AppImageBuilder.yml --skip-test
else
    appimage-builder --appdir build/AppDir --build-dir build/appimage-build --recipe AppImageBuilder.yml --skip-test
fi

mv ./AssemblyLineLauncher*.AppImage ./build/