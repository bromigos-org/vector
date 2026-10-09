#!/usr/bin/env bash
# Root setup in a fresh archlinux:base-devel container, before publish.sh:
# the tools publish.sh needs, and an unprivileged `builder` (makepkg refuses
# root) that may run pacman through sudo to install build dependencies.
# Usage: prepare-container.sh <checkout dir>
set -euo pipefail
checkout=${1:?usage: prepare-container.sh <checkout dir>}

pacman -Syu --noconfirm --needed git rclone >/dev/null
id builder >/dev/null 2>&1 || useradd --create-home --user-group builder
echo 'builder ALL=(root) NOPASSWD: /usr/bin/pacman' > /etc/sudoers.d/builder
chmod 0440 /etc/sudoers.d/builder
# The checkout belongs to whoever ran actions/checkout; builder owns it now
# (pkgver() rewrites each PKGBUILD) and git must accept that.
chown -R builder:builder "$checkout"
git config --system --add safe.directory "$checkout"
