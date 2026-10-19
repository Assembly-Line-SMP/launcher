#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
build_dir="${project_dir}/build-native"
prefix="${build_dir}/install"
venv_dir="${project_dir}/.venv"

if [[ ! -x "${venv_dir}/bin/python" ]]; then
	python3 -m venv "${venv_dir}"
fi

"${venv_dir}/bin/python" -m pip install -r "${project_dir}/requirements.txt"
export PATH="${venv_dir}/bin:${PATH}"

if [[ -f "${build_dir}/meson-private/coredata.dat" ]]; then
	meson setup --reconfigure "${build_dir}" --prefix="${prefix}"
else
	meson setup "${build_dir}" --prefix="${prefix}"
fi

meson compile -C "${build_dir}"
meson install -C "${build_dir}"

exec "${prefix}/bin/assemblylinelauncher" "$@"