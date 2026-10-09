#!/usr/bin/env bash
# Build claude-revoke packages (.deb, .rpm and Arch) with nfpm.
#
#   packaging/build.sh          stage the files, fetch nfpm if needed, build into dist/
#   packaging/build.sh stage    only create dist/stage/ (no network, no nfpm)
#
# The version is read from __version__ in claude_revoke.py. Override the output
# folder with DIST=<dir>.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dist="${DIST:-$root/dist}"
stage="$dist/stage"

fail() { echo "build.sh: $*" >&2; exit 1; }

version() {
    local v
    v="$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' "$root/claude_revoke.py")"
    [ -n "$v" ] || fail "could not read __version__ from claude_revoke.py"
    echo "$v"
}

put() {   # put <repo file> <path inside the package> <mode>
    [ -f "$root/$1" ] || fail "missing source file $1"
    install -D -m "$3" "$root/$1" "$stage$2"
}

do_stage() {
    local v f
    v="$(version)"
    # Check every input before touching dist/, so a failure leaves no half-staged tree.
    for f in claude_revoke.py assets/logo.svg README.md CHANGELOG.md LICENSE packaging/claude-revoke.desktop.in; do
        [ -f "$root/$f" ] || fail "missing source file $f"
    done
    rm -rf "$stage"
    put claude_revoke.py /usr/bin/claude-revoke 755
    put assets/logo.svg /usr/share/icons/hicolor/scalable/apps/claude-revoke.svg 644
    put README.md /usr/share/doc/claude-revoke/README.md 644
    put CHANGELOG.md /usr/share/doc/claude-revoke/CHANGELOG.md 644
    put LICENSE /usr/share/doc/claude-revoke/LICENSE 644
    put LICENSE /usr/share/doc/claude-revoke/copyright 644     # the name Debian policy expects
    # A package cannot pick a terminal emulator at install time the way
    # install.sh does, so the desktop's default terminal is used.
    install -d -m 755 "$stage/usr/share/applications"
    sed -e 's|^Exec=.*|Exec=claude-revoke --pause|' -e 's|^Terminal=false$|Terminal=true|' \
        "$root/packaging/claude-revoke.desktop.in" > "$stage/usr/share/applications/claude-revoke.desktop"
    chmod 644 "$stage/usr/share/applications/claude-revoke.desktop"
    echo "staged $v in $stage"
}

case "${1:-build}" in
    stage) do_stage ;;
    build) fail "the build step is not implemented yet" ;;
    *) echo "usage: $0 [stage|build]" >&2; exit 2 ;;
esac
