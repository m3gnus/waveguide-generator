#!/bin/bash
# Remove a Waveguide Generator installation made by install.sh. This file ships
# INSIDE Waveguide.Generator-<version>-linux-x86_64.tar.gz and is copied into
# the installation, so it can be run without keeping the download. It is not
# the source uninstaller, which is installers/linux/uninstall.sh in the
# checkout and removes a .venv from a Git clone.
#
# It removes exactly what install.sh created and nothing else: the application
# directory, the menu entry, the icon, and the PATH symlink -- and the symlink
# only when it still points into the directory being removed, so an uninstall
# cannot take a command belonging to a second installation with it.
#
# Designs, job history, meshes and logs live outside the installation and are
# kept unless --data says otherwise, because reinstalling is the common reason
# to run this and losing a workspace to it would be unrecoverable.

set -u

BUNDLE_DIRECTORY="waveguide-generator"
LAUNCHER_NAME="waveguide-generator"
DESKTOP_ENTRY_NAME="waveguide-generator.desktop"
ICON_NAME="waveguide-generator.png"
ICON_SIZE="512x512"
DESKTOP_OWNER_NAME=".waveguide-generator.owner"
ICON_OWNER_NAME=".waveguide-generator.owner"
#: server/platform/paths.py: the XDG data directory the application itself uses.
DATA_DIRECTORY="WaveguideGenerator"

HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
HOME_DIRECTORY="${HOME:-}"
DATA_HOME="${XDG_DATA_HOME:-$HOME_DIRECTORY/.local/share}"

# Run from inside an installation, that installation is the one to remove.
# Run from an extracted tarball, fall back to the default location, which is
# where install.sh with no --prefix put it.
if [ -e "$HERE/app/APP-MANIFEST.json" ]; then
    TARGET="$HERE"
else
    TARGET="$DATA_HOME/$BUNDLE_DIRECTORY"
fi
REMOVE_DATA=0

usage() {
    cat <<'USAGE'
Usage: ./uninstall.sh [--prefix DIR] [--data]

  --prefix DIR   remove the installation in DIR/waveguide-generator
                 (default: this installation, or $XDG_DATA_HOME/waveguide-generator)
  --data         also remove designs, job history, meshes and logs
USAGE
}

fail() {
    printf '\n'
    printf '===============================================================\n'
    printf '%s\n' "$@"
    printf '===============================================================\n'
    printf '\n'
    exit 1
}

canonical_path() {
    local input="$1" part resolved candidate
    local -a components
    if realpath -m -- "$input" 2>/dev/null; then
        return
    fi
    IFS='/' read -r -a components <<< "$input"
    resolved="/"
    for part in "${components[@]}"; do
        case "$part" in
            ''|.) ;;
            ..)
                [ "$resolved" = "/" ] || resolved="${resolved%/*}"
                [ -n "$resolved" ] || resolved="/"
                ;;
            *)
                if [ "$resolved" = "/" ]; then candidate="/$part"; else candidate="$resolved/$part"; fi
                if [ -e "$candidate" ] || [ -L "$candidate" ]; then
                    resolved="$(realpath -- "$candidate")" || return
                else
                    resolved="$candidate"
                fi
                ;;
        esac
    done
    printf '%s\n' "$resolved"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --prefix)
            [ "$#" -ge 2 ] || fail "--prefix needs a directory."
            TARGET="$2/$BUNDLE_DIRECTORY"
            shift 2
            ;;
        --prefix=*)
            TARGET="${1#--prefix=}/$BUNDLE_DIRECTORY"
            shift
            ;;
        --data)
            REMOVE_DATA=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            fail "Unknown option: $1"
            ;;
    esac
done

[ -n "$HOME_DIRECTORY" ] || fail "HOME is not set." "Nothing has been removed."
case "$HOME_DIRECTORY" in /*) ;; *) fail "HOME must be an absolute path: $HOME_DIRECTORY" "Nothing has been removed." ;; esac
case "$DATA_HOME" in /*) ;; *) fail "XDG_DATA_HOME must be an absolute path: $DATA_HOME" "Nothing has been removed." ;; esac
RESOLVED="$(canonical_path "$HOME_DIRECTORY")" || fail "Could not resolve HOME: $HOME_DIRECTORY"
HOME_DIRECTORY="$RESOLVED"
RESOLVED="$(canonical_path "$DATA_HOME")" || fail "Could not resolve XDG_DATA_HOME: $DATA_HOME"
DATA_HOME="$RESOLVED"
case "$TARGET" in /*) ;; *) fail "--prefix must be an absolute path: ${TARGET%/$BUNDLE_DIRECTORY}" "Nothing has been removed." ;; esac
TARGET="$(canonical_path "$TARGET")" || fail "Could not resolve the installation path."

case "$TARGET$DATA_HOME$HOME_DIRECTORY" in
    *$'\n'*|*$'\r'*) fail "Removal paths cannot contain a newline." "Nothing has been removed." ;;
esac

if [ ! -d "$TARGET" ]; then
    printf 'Nothing to remove: %s does not exist.\n' "$TARGET"
    exit 0
fi

# `rm -rf` on a path assembled from an option is exactly the shape that removes
# someone's home directory when the option is wrong. Require the manifest the
# builder writes, so only a directory this project produced can be removed.
if [ ! -e "$TARGET/app/APP-MANIFEST.json" ]; then
    fail "$TARGET is not a Waveguide Generator installation." \
         "" \
         "Refusing to remove it. Nothing has been changed."
fi

printf '\n'
printf 'Removing Waveguide Generator\n'
printf '============================\n'
printf '\n'

BIN_LINK="$HOME_DIRECTORY/.local/bin/$LAUNCHER_NAME"
if [ -L "$BIN_LINK" ]; then
    LINKED="$(readlink -f "$BIN_LINK" 2>/dev/null || printf '')"
    RESOLVED_TARGET="$(cd -- "$TARGET" && pwd -P)/$LAUNCHER_NAME"
    if [ "$LINKED" = "$RESOLVED_TARGET" ]; then
        rm -f "$BIN_LINK" && printf 'Removed the command: %s\n' "$BIN_LINK"
    else
        printf 'Left %s alone: it points at %s, not at this installation.\n' \
            "$BIN_LINK" "${LINKED:-an unreadable path}"
    fi
fi

DESKTOP="$DATA_HOME/applications/$DESKTOP_ENTRY_NAME"
DESKTOP_OWNER="$DATA_HOME/applications/$DESKTOP_OWNER_NAME"
ICON="$DATA_HOME/icons/hicolor/$ICON_SIZE/apps/$ICON_NAME"
ICON_OWNER="$DATA_HOME/icons/hicolor/$ICON_SIZE/apps/$ICON_OWNER_NAME"

owned_by_target() {
    [ -f "$1" ] || return 1
    owner="$(sed -n '1p' -- "$1")"
    [ "$owner" = "$TARGET" ]
}

if owned_by_target "$DESKTOP_OWNER"; then
    rm -f -- "$DESKTOP" "$DESKTOP_OWNER" && printf 'Removed the menu entry: %s\n' "$DESKTOP"
elif [ -e "$DESKTOP" ]; then
    printf 'Left the menu entry alone: it belongs to another installation.\n'
fi

if owned_by_target "$ICON_OWNER"; then
    rm -f -- "$ICON" "$ICON_OWNER" && printf 'Removed the icon: %s\n' "$ICON"
elif [ -e "$ICON" ]; then
    printf 'Left the icon alone: it belongs to another installation.\n'
fi

# Last, because everything above identifies itself by pointing at it.
if ! rm -rf "$TARGET"; then
    fail "Could not remove $TARGET." \
         "Quit Waveguide Generator if it is running, then try again."
fi
printf 'Removed the application: %s\n' "$TARGET"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DATA_HOME/applications" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
fi

DATA_PATH="$DATA_HOME/$DATA_DIRECTORY"
if [ "$REMOVE_DATA" -eq 1 ]; then
    if [ -d "$DATA_PATH" ]; then
        rm -rf "$DATA_PATH" && printf 'Removed your designs and job history: %s\n' "$DATA_PATH"
    fi
elif [ -d "$DATA_PATH" ]; then
    printf '\nYour designs, job history, meshes and logs were kept in:\n  %s\n' "$DATA_PATH"
    printf 'Run this again with --data to remove them too.\n'
fi

printf '\nWaveguide Generator has been removed.\n\n'
exit 0
