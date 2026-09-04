#!/bin/bash
# Install Waveguide Generator from the release tarball. This file ships INSIDE
# Waveguide.Generator-<version>-linux-x86_64.tar.gz, beside the application
# folder; it is not the source installer, which is installers/linux/install.sh
# in the checkout and builds a .venv from a Git clone.
#
# WHY THE INSTALL IS PER-USER.
#
# Waveguide Generator updates itself by replacing `app` and `runtime` inside
# its own installation directory, and the updater runs as the user with no way
# to elevate (launchers/apply_update.py). A root-owned copy under /opt or
# /usr/local would therefore install once and then refuse every update it was
# offered, which is the failure the Windows installer avoids by writing to
# %LOCALAPPDATA%\Programs rather than Program Files. Linux gets the same
# answer for the same reason: ~/.local, no root, no package manager.
#
# It must stay self-contained. It runs from wherever the user extracted the
# tarball, with nothing from the checkout beside it, and its only dependencies
# are bash and coreutils.

set -u

BUNDLE_DIRECTORY="waveguide-generator"
LAUNCHER_NAME="waveguide-generator"
DESKTOP_ENTRY_NAME="waveguide-generator.desktop"
ICON_NAME="waveguide-generator.png"
ICON_SIZE="512x512"
UNINSTALLER_NAME="uninstall.sh"
DESKTOP_OWNER_NAME=".waveguide-generator.owner"
ICON_OWNER_NAME=".waveguide-generator.owner"

HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
SOURCE="$HERE/$BUNDLE_DIRECTORY"

HOME_DIRECTORY="${HOME:-}"
DATA_HOME="${XDG_DATA_HOME:-$HOME_DIRECTORY/.local/share}"
PREFIX="$DATA_HOME"
LAUNCH=1
PREFLIGHT=1

usage() {
    cat <<'USAGE'
Usage: ./install.sh [--prefix DIR] [--no-launch] [--skip-checks]

  --prefix DIR   install into DIR/waveguide-generator
                 (default: $XDG_DATA_HOME, or ~/.local/share)
  --no-launch    install without starting the application afterwards
  --skip-checks  install even if the system-library check below fails
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
    # BSD realpath has no -m; this branch keeps cross-platform fixture tests
    # useful. Resolve each existing component before applying the next one so
    # link/../target follows filesystem semantics rather than lexical cleanup.
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

# Exec is not shell syntax. The desktop-entry specification first decodes the
# generic string escapes and then its own quoting, so a literal backslash needs
# four backslashes in a quoted argument. Quotes, dollar signs and backticks
# need two.
desktop_exec_escape() {
    local value="$1" output="" character
    while [ -n "$value" ]; do
        character="${value:0:1}"
        value="${value:1}"
        case "$character" in
            '\') output+='\\\\' ;;
            '"') output+='\\"' ;;
            '$') output+='\\$' ;;
            '`') output+='\\`' ;;
            *) output+="$character" ;;
        esac
    done
    printf '%s' "$output"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --prefix)
            [ "$#" -ge 2 ] || fail "--prefix needs a directory."
            PREFIX="$2"
            shift 2
            ;;
        --prefix=*)
            PREFIX="${1#--prefix=}"
            shift
            ;;
        --no-launch)
            LAUNCH=0
            shift
            ;;
        --skip-checks)
            PREFLIGHT=0
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

[ -n "$HOME_DIRECTORY" ] || fail "HOME is not set." "Nothing has been installed."
case "$HOME_DIRECTORY" in /*) ;; *) fail "HOME must be an absolute path: $HOME_DIRECTORY" "Nothing has been installed." ;; esac
case "$DATA_HOME" in /*) ;; *) fail "XDG_DATA_HOME must be an absolute path: $DATA_HOME" "Nothing has been installed." ;; esac
case "$PREFIX" in /*) ;; *) fail "--prefix must be an absolute path: $PREFIX" "Nothing has been installed." ;; esac
RESOLVED="$(canonical_path "$HOME_DIRECTORY")" || fail "Could not resolve HOME: $HOME_DIRECTORY"
HOME_DIRECTORY="$RESOLVED"
RESOLVED="$(canonical_path "$DATA_HOME")" || fail "Could not resolve XDG_DATA_HOME: $DATA_HOME"
DATA_HOME="$RESOLVED"
RESOLVED="$(canonical_path "$PREFIX")" || fail "Could not resolve --prefix: $PREFIX"
PREFIX="$RESOLVED"

case "$PREFIX$DATA_HOME$HOME_DIRECTORY" in
    *$'\n'*|*$'\r'*)
        fail "Installation paths cannot contain a newline." "Nothing has been installed."
        ;;
esac

if [ "$(id -u)" = "0" ]; then
    fail "Do not install Waveguide Generator as root." \
         "" \
         "It replaces files inside its own installation when it updates, and it" \
         "cannot ask for a password to do that. A root-owned copy would install" \
         "once and then refuse every update. Run this as your normal user; it" \
         "installs under your home directory and needs no privileges."
fi

if [ ! -d "$SOURCE" ]; then
    fail "\"$BUNDLE_DIRECTORY\" is not beside this installer." \
         "Looked in: $HERE" \
         "" \
         "Extract the whole tarball and run install.sh from inside the" \
         "extracted folder, without moving it somewhere else first."
fi

# A folder with the right name is not evidence of the right contents, and
# everything below this point copies and deletes directories.
for required in "app/APP-MANIFEST.json" "runtime/RUNTIME-MANIFEST.json" \
                "$LAUNCHER_NAME" "$DESKTOP_ENTRY_NAME" "$ICON_NAME"; do
    [ -e "$SOURCE/$required" ] || \
        fail "The application folder beside this installer is incomplete:" \
             "$SOURCE/$required is missing." \
             "" \
             "Download the release tarball again and re-extract it."
done
[ -f "$HERE/$UNINSTALLER_NAME" ] || \
    fail "The uninstaller is not beside this installer: $HERE/$UNINSTALLER_NAME" \
         "Download the release tarball again and re-extract it."
SOURCE="$(canonical_path "$SOURCE")" || fail "Could not resolve the extracted application path."

# The libraries this bundle does not bring with it.
#
# Everything else is inside: the interpreter, Tcl/Tk, and every Python package.
# gmsh is the exception -- its wheel dlopens the system OpenGL and X11 client
# libraries at import -- and gmsh is the single geometry authority in this
# application, so an install without them opens the interface and meshes
# nothing.
#
# A desktop system already has all of these; a server or container image does
# not. Measured 2026-09-04 against the pinned runtime on a bare ubuntu:24.04
# image, adding one package at a time until `import gmsh` succeeded, which is
# where the list below comes from.
#
# Checked by importing gmsh rather than by looking for package names, because
# the import is the thing that has to work, the error names the exact library,
# and the packages that provide them differ on every distribution.
if [ "$PREFLIGHT" -eq 1 ]; then
    printf 'Checking system libraries ...\n'
    if ! PREFLIGHT_ERROR="$("$SOURCE/runtime/bin/python3.13" -c 'import gmsh' 2>&1)"; then
        fail "Waveguide Generator's mesher cannot load a library this system does not have:" \
             "" \
             "$(printf '%s' "$PREFLIGHT_ERROR" | tail -n 1)" \
             "" \
             "These are ordinary OpenGL and X11 desktop libraries. On Ubuntu 24.04" \
             "and Debian, this installs every one the mesher needs:" \
             "" \
             "  sudo apt install libglu1-mesa libgl1 libgomp1 libfontconfig1 \\" \
             "                   libxrender1 libxcursor1 libxft2 libxinerama1 \\" \
             "                   libxi6 libxext6" \
             "" \
             "On Fedora and Arch the same libraries are packaged under their own" \
             "names -- mesa-libGLU / glu and the matching libX* packages." \
             "" \
             "Then run this installer again. Nothing has been installed yet." \
             "Use --skip-checks to install anyway."
    fi
fi

TARGET="$PREFIX/$BUNDLE_DIRECTORY"
APPLICATIONS="$DATA_HOME/applications"
ICONS="$DATA_HOME/icons/hicolor/$ICON_SIZE/apps"
BIN="$HOME_DIRECTORY/.local/bin"
TARGET="$(canonical_path "$TARGET")" || fail "Could not resolve the installation path."

# The desktop-entry specification spells a literal percent as %%, but GLib on
# the supported Ubuntu 24.04 target rejects that sequence in the executable
# path and discards Exec entirely. A raw percent launches in GLib but fails
# desktop-file-validate. There is no representation accepted by both, so stop
# before mutation instead of installing a menu entry that cannot launch.
case "$TARGET" in
    *%*)
        fail "The installation path cannot contain a percent sign: $TARGET" \
             "Choose another location with --prefix. Nothing has been installed."
        ;;
esac

if [ "$TARGET" = "$SOURCE" ]; then
    fail "The source and installation directory are the same: $TARGET" \
         "Choose another location with --prefix. Nothing has been installed."
fi
case "$TARGET" in
    "$SOURCE"/*)
        fail "The installation directory is inside the extracted application: $TARGET" \
             "Choose another location with --prefix. Nothing has been installed."
        ;;
esac
case "$SOURCE" in
    "$TARGET"/*)
        fail "The extracted application is inside the installation directory: $SOURCE" \
             "Choose another location with --prefix. Nothing has been installed."
        ;;
esac

# Validate an existing target before creating even the shared destination
# directories, and before any rename can make it disappear.
if [ -e "$TARGET" ]; then
    [ -e "$TARGET/app/APP-MANIFEST.json" ] || \
        fail "$TARGET already exists and is not a Waveguide Generator installation." \
             "" \
             "Refusing to replace it. Choose another location with --prefix, or" \
             "remove that directory yourself if you know what it is."
fi

printf '\n'
printf 'Installing Waveguide Generator\n'
printf '==============================\n'
printf '\n'
printf 'Application: %s\n' "$TARGET"
printf 'Menu entry:  %s/%s\n' "$APPLICATIONS" "$DESKTOP_ENTRY_NAME"
printf 'Command:     %s/%s\n' "$BIN" "$LAUNCHER_NAME"
printf '\n'

mkdir -p "$PREFIX" "$APPLICATIONS" "$ICONS" "$BIN" || \
    fail "Could not create the installation directories under $HOME_DIRECTORY."

# Build and validate every new artefact before moving the current installation.
# Files are staged on the same filesystems as their final names, so the commit
# below consists only of renames and can restore every displaced predecessor.
STAGE_ROOT="$(mktemp -d "$PREFIX/.waveguide-generator.install.XXXXXX")" || \
    fail "Could not create a staging directory under $PREFIX."
STAGED_TARGET="$STAGE_ROOT/$BUNDLE_DIRECTORY"
STAGED_DESKTOP="$(mktemp "$APPLICATIONS/.waveguide-generator.XXXXXX.desktop")" || \
    { rm -rf -- "$STAGE_ROOT"; fail "Could not stage the desktop entry under $APPLICATIONS."; }
STAGED_ICON="$(mktemp "$ICONS/.waveguide-generator.icon.XXXXXX")" || \
    { rm -rf -- "$STAGE_ROOT" "$STAGED_DESKTOP"; fail "Could not stage the icon under $ICONS."; }
STAGED_DESKTOP_OWNER="$(mktemp "$APPLICATIONS/.waveguide-generator.owner.XXXXXX")" || \
    { rm -rf -- "$STAGE_ROOT" "$STAGED_DESKTOP" "$STAGED_ICON"; fail "Could not stage desktop ownership under $APPLICATIONS."; }
STAGED_ICON_OWNER="$(mktemp "$ICONS/.waveguide-generator.owner.XXXXXX")" || \
    { rm -rf -- "$STAGE_ROOT" "$STAGED_DESKTOP" "$STAGED_ICON" "$STAGED_DESKTOP_OWNER"; fail "Could not stage icon ownership under $ICONS."; }

COMMITTED=0
TARGET_INSTALLED=0
DESKTOP_INSTALLED=0
ICON_INSTALLED=0
DESKTOP_OWNER_INSTALLED=0
ICON_OWNER_INSTALLED=0
LINK_INSTALLED=0
TARGET_HAD=0
DESKTOP_HAD=0
ICON_HAD=0
DESKTOP_OWNER_HAD=0
ICON_OWNER_HAD=0
LINK_HAD=0
DISPLACED_TARGET=""
BACKUP_DESKTOP=""
BACKUP_ICON=""
BACKUP_DESKTOP_OWNER=""
BACKUP_ICON_OWNER=""
BACKUP_LINK=""

rollback() {
    status=$?
    trap - EXIT HUP INT TERM
    RESTORE_FAILED=0
    restore_backup() {
        backup="$1"
        destination="$2"
        description="$3"
        if [ -e "$backup" ] || [ -L "$backup" ]; then
            if [ -e "$destination" ] || [ -L "$destination" ]; then
                printf 'ERROR: cannot restore the previous %s while its destination remains.\n' "$description" >&2
                printf 'Its backup remains at: %s\n' "$backup" >&2
                RESTORE_FAILED=1
                return
            fi
            if ! mv -- "$backup" "$destination"; then
                printf 'ERROR: could not restore the previous %s.\n' "$description" >&2
                printf 'Its backup remains at: %s\n' "$backup" >&2
                RESTORE_FAILED=1
            fi
        fi
    }
    if [ "$COMMITTED" -ne 1 ]; then
        if [ "$TARGET_INSTALLED" -eq 1 ] && ! rm -rf -- "$TARGET"; then
            printf 'ERROR: could not remove the incomplete application at: %s\n' "$TARGET" >&2
            RESTORE_FAILED=1
        fi
        if [ "$TARGET_HAD" -eq 1 ] && [ -n "$DISPLACED_TARGET" ]; then
            restore_backup "$DISPLACED_TARGET" "$TARGET" "application"
        fi
        if [ "$DESKTOP_INSTALLED" -eq 1 ]; then rm -f -- "$APPLICATIONS/$DESKTOP_ENTRY_NAME"; fi
        if [ "$DESKTOP_HAD" -eq 1 ] && [ -n "$BACKUP_DESKTOP" ]; then
            restore_backup "$BACKUP_DESKTOP" "$APPLICATIONS/$DESKTOP_ENTRY_NAME" "desktop entry"
        fi
        if [ "$ICON_INSTALLED" -eq 1 ]; then rm -f -- "$ICONS/$ICON_NAME"; fi
        if [ "$ICON_HAD" -eq 1 ] && [ -n "$BACKUP_ICON" ]; then
            restore_backup "$BACKUP_ICON" "$ICONS/$ICON_NAME" "icon"
        fi
        if [ "$DESKTOP_OWNER_INSTALLED" -eq 1 ]; then rm -f -- "$APPLICATIONS/$DESKTOP_OWNER_NAME"; fi
        if [ "$DESKTOP_OWNER_HAD" -eq 1 ] && [ -n "$BACKUP_DESKTOP_OWNER" ]; then
            restore_backup "$BACKUP_DESKTOP_OWNER" "$APPLICATIONS/$DESKTOP_OWNER_NAME" "desktop ownership marker"
        fi
        if [ "$ICON_OWNER_INSTALLED" -eq 1 ]; then rm -f -- "$ICONS/$ICON_OWNER_NAME"; fi
        if [ "$ICON_OWNER_HAD" -eq 1 ] && [ -n "$BACKUP_ICON_OWNER" ]; then
            restore_backup "$BACKUP_ICON_OWNER" "$ICONS/$ICON_OWNER_NAME" "icon ownership marker"
        fi
        if [ "$LINK_INSTALLED" -eq 1 ]; then rm -f -- "$BIN/$LAUNCHER_NAME"; fi
        if [ "$LINK_HAD" -eq 1 ] && [ -n "$BACKUP_LINK" ]; then
            restore_backup "$BACKUP_LINK" "$BIN/$LAUNCHER_NAME" "command link"
        fi
        if [ "$TARGET_HAD" -eq 1 ] || [ "$DESKTOP_HAD" -eq 1 ] || \
           [ "$ICON_HAD" -eq 1 ] || [ "$LINK_HAD" -eq 1 ]; then
            if [ "$RESTORE_FAILED" -eq 0 ]; then
                printf 'Restored the previous installation and desktop integration.\n'
            else
                printf 'ERROR: rollback was incomplete; the backup paths above were preserved.\n' >&2
            fi
        fi
    fi
    rm -rf -- "$STAGE_ROOT"
    rm -f -- "$STAGED_DESKTOP" "$STAGED_ICON" \
        "$STAGED_DESKTOP_OWNER" "$STAGED_ICON_OWNER"
    exit "$status"
}
trap rollback EXIT
trap 'exit 130' HUP INT TERM

printf 'Staging the application (this takes a moment) ...\n'
cp -a -- "$SOURCE" "$STAGED_TARGET" || \
    fail "Could not stage the application under $PREFIX." \
         "Check that there is enough free space and that $PREFIX is writable."
cp -- "$HERE/$UNINSTALLER_NAME" "$STAGED_TARGET/$UNINSTALLER_NAME" || \
    fail "Could not add the uninstaller to the staged application."
chmod 755 "$STAGED_TARGET/$UNINSTALLER_NAME" || \
    fail "Could not make the staged uninstaller executable."

EXECUTABLE="$(desktop_exec_escape "$TARGET/$LAUNCHER_NAME")"
DESKTOP_RENDERED=0
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        "Exec=@INSTALL_DIR@/$LAUNCHER_NAME %U")
            printf 'Exec="%s" %%U\n' "$EXECUTABLE"
            DESKTOP_RENDERED=1
            ;;
        *) printf '%s\n' "$line" ;;
    esac
done < "$STAGED_TARGET/$DESKTOP_ENTRY_NAME" > "$STAGED_DESKTOP" || \
    fail "Could not render the desktop entry under $APPLICATIONS."
[ "$DESKTOP_RENDERED" -eq 1 ] || \
    fail "The staged desktop entry does not contain the expected Exec template."
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$STAGED_DESKTOP" || \
        fail "The rendered desktop entry failed desktop-file-validate."
fi
chmod 644 "$STAGED_DESKTOP" || fail "Could not set the desktop entry permissions."
cp -- "$STAGED_TARGET/$ICON_NAME" "$STAGED_ICON" || fail "Could not stage the application icon."
chmod 644 "$STAGED_ICON" || fail "Could not set the icon permissions."
printf '%s\n' "$TARGET" > "$STAGED_DESKTOP_OWNER" || fail "Could not record desktop ownership."
printf '%s\n' "$TARGET" > "$STAGED_ICON_OWNER" || fail "Could not record icon ownership."

# Refuse to overwrite an unrelated command. Symlinks are the form this
# installer owns, including a broken link left by an older installation.
if { [ -e "$BIN/$LAUNCHER_NAME" ] || [ -L "$BIN/$LAUNCHER_NAME" ]; } && \
   [ ! -L "$BIN/$LAUNCHER_NAME" ]; then
    fail "$BIN/$LAUNCHER_NAME already exists and is not a symlink." \
         "Move it yourself before installing. Nothing has been replaced."
fi

# Prepare unused backup names in the same directories as their final paths.
DISPLACED_TARGET="$(mktemp -d "$PREFIX/.waveguide-generator.previous.XXXXXX")" || \
    fail "Could not reserve a rollback path under $PREFIX."
rmdir "$DISPLACED_TARGET" || fail "Could not prepare the application rollback path."
BACKUP_DESKTOP="$(mktemp "$APPLICATIONS/.waveguide-generator.desktop.backup.XXXXXX")" || fail "Could not prepare desktop rollback."
BACKUP_ICON="$(mktemp "$ICONS/.waveguide-generator.icon.backup.XXXXXX")" || fail "Could not prepare icon rollback."
BACKUP_DESKTOP_OWNER="$(mktemp "$APPLICATIONS/.waveguide-generator.owner.backup.XXXXXX")" || fail "Could not prepare desktop-owner rollback."
BACKUP_ICON_OWNER="$(mktemp "$ICONS/.waveguide-generator.owner.backup.XXXXXX")" || fail "Could not prepare icon-owner rollback."
BACKUP_LINK="$(mktemp "$BIN/.waveguide-generator.link.backup.XXXXXX")" || fail "Could not prepare command rollback."
rm -f -- "$BACKUP_DESKTOP" "$BACKUP_ICON" "$BACKUP_DESKTOP_OWNER" "$BACKUP_ICON_OWNER" "$BACKUP_LINK"

printf 'Committing the staged installation ...\n'
if [ -e "$TARGET" ]; then
    TARGET_HAD=1
    mv -- "$TARGET" "$DISPLACED_TARGET" || fail "Could not move the existing installation aside."
fi
mv -- "$STAGED_TARGET" "$TARGET" || fail "Could not put the staged application in $TARGET."
TARGET_INSTALLED=1

if [ -e "$APPLICATIONS/$DESKTOP_ENTRY_NAME" ] || [ -L "$APPLICATIONS/$DESKTOP_ENTRY_NAME" ]; then
    DESKTOP_HAD=1
    mv -- "$APPLICATIONS/$DESKTOP_ENTRY_NAME" "$BACKUP_DESKTOP" || fail "Could not back up the current desktop entry."
fi
mv -- "$STAGED_DESKTOP" "$APPLICATIONS/$DESKTOP_ENTRY_NAME" || fail "Could not install the rendered desktop entry."
DESKTOP_INSTALLED=1

if [ -e "$ICONS/$ICON_NAME" ] || [ -L "$ICONS/$ICON_NAME" ]; then
    ICON_HAD=1
    mv -- "$ICONS/$ICON_NAME" "$BACKUP_ICON" || fail "Could not back up the current icon."
fi
mv -- "$STAGED_ICON" "$ICONS/$ICON_NAME" || fail "Could not install the application icon."
ICON_INSTALLED=1

if [ -e "$APPLICATIONS/$DESKTOP_OWNER_NAME" ] || [ -L "$APPLICATIONS/$DESKTOP_OWNER_NAME" ]; then
    DESKTOP_OWNER_HAD=1
    mv -- "$APPLICATIONS/$DESKTOP_OWNER_NAME" "$BACKUP_DESKTOP_OWNER" || fail "Could not back up desktop ownership."
fi
mv -- "$STAGED_DESKTOP_OWNER" "$APPLICATIONS/$DESKTOP_OWNER_NAME" || fail "Could not install desktop ownership."
DESKTOP_OWNER_INSTALLED=1

if [ -e "$ICONS/$ICON_OWNER_NAME" ] || [ -L "$ICONS/$ICON_OWNER_NAME" ]; then
    ICON_OWNER_HAD=1
    mv -- "$ICONS/$ICON_OWNER_NAME" "$BACKUP_ICON_OWNER" || fail "Could not back up icon ownership."
fi
mv -- "$STAGED_ICON_OWNER" "$ICONS/$ICON_OWNER_NAME" || fail "Could not install icon ownership."
ICON_OWNER_INSTALLED=1

if [ -L "$BIN/$LAUNCHER_NAME" ]; then
    LINK_HAD=1
    mv -- "$BIN/$LAUNCHER_NAME" "$BACKUP_LINK" || fail "Could not back up the current command link."
fi
ln -s -- "$TARGET/$LAUNCHER_NAME" "$BIN/$LAUNCHER_NAME" || fail "Could not install the command link."
LINK_INSTALLED=1

COMMITTED=1
rm -rf -- "$DISPLACED_TARGET"
rm -f -- "$BACKUP_DESKTOP" "$BACKUP_ICON" "$BACKUP_DESKTOP_OWNER" \
    "$BACKUP_ICON_OWNER" "$BACKUP_LINK"

# Best effort, and genuinely optional: every current desktop notices a new
# .desktop file on its own, and these tools are absent on minimal systems.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
fi

printf '\n'
printf 'Installed: %s\n' "$TARGET"
printf '\n'

case ":${PATH}:" in
    *":$BIN:"*) ;;
    *)
        printf '%s is not on your PATH, so the "%s" command will not be found\n' \
            "$BIN" "$LAUNCHER_NAME"
        printf 'until you add it. The menu entry works either way.\n\n'
        ;;
esac

printf 'To remove it later:\n'
printf '  %s/%s          (add --data to remove your designs and job history too)\n\n' \
    "$TARGET" "$UNINSTALLER_NAME"

if [ "$LAUNCH" -eq 1 ]; then
    printf 'Starting Waveguide Generator ...\n'
    "$TARGET/$LAUNCHER_NAME" >/dev/null 2>&1 &
fi
exit 0
