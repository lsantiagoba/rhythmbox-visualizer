#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
user_home="$(getent passwd "$(id -u)" | cut -d: -f6)"
data_home="${XDG_DATA_HOME:-$user_home/.local/share}"

# Sandboxed editors (notably the VS Code snap) replace XDG_DATA_HOME with a
# private directory that desktop Rhythmbox never scans.
case "$data_home" in
    "$user_home"/snap/*) data_home="$user_home/.local/share" ;;
esac

plugin_dir="$data_home/rhythmbox/plugins/visualizer"

mkdir -p "$plugin_dir"
install -m 0644 "$script_dir/visualizer.py" "$plugin_dir/visualizer.py"
install -m 0644 "$script_dir/visualizer.plugin" "$plugin_dir/visualizer.plugin"

echo "Installed in $plugin_dir"
echo "Restart Rhythmbox and enable Visualizer in Plugins."
