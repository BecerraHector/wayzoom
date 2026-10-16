#!/usr/bin/env bash
# Install WayZoom for the current user by symlinking the launcher into ~/.local/bin.
set -euo pipefail
repo_root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
target_dir="${HOME}/.local/bin"
target="${target_dir}/wayzoom"
source_bin="${repo_root}/bin/wayzoom"

mkdir -p "$target_dir"
if [[ -e "$target" && ! -L "$target" ]]; then
    echo "error: $target exists and is not a symlink; refusing to overwrite it" >&2
    exit 1
fi
ln -sfn "$source_bin" "$target"
echo "installed: $target -> $source_bin"

for tool in wf-recorder slurp ffmpeg ffprobe hyprctl notify-send; do
    command -v "$tool" >/dev/null 2>&1 || echo "warning: '$tool' not found on PATH"
done
case ":${PATH}:" in
    *":${target_dir}:"*) ;;
    *) echo "warning: ${target_dir} is not on PATH; add it to use 'wayzoom' directly" ;;
esac
