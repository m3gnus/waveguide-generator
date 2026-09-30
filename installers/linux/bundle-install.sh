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

# Exit status:
#   0  installed
#   1  failed; the previous installation and desktop integration were kept or
#      restored (or no previous installation existed), including HUP/INT/TERM
#   3  ROLLBACK INCOMPLETE: a previous installation or desktop integration
#      could not be restored. Backup paths are printed and left in place.

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
UPDATE=0

usage() {
    cat <<'USAGE'
Usage: ./install.sh [--prefix DIR] [--no-launch] [--skip-checks] [--update]

  --prefix DIR   install into DIR/waveguide-generator
                 (default: $XDG_DATA_HOME, or ~/.local/share)
  --no-launch    install without starting the application afterwards
  --skip-checks  install even if the system-library check below fails
  --update       replace an existing installation in place, without starting it;
                 fails if DIR/waveguide-generator is not already installed
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
        --update)
            UPDATE=1
            LAUNCH=0
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
             "These are ordinary OpenGL and X11 desktop libraries. Pick the line" \
             "for your distribution; each installs every one the mesher needs." \
             "" \
             "  Ubuntu 24.04 / Debian:" \
             "  sudo apt install libglu1-mesa libgl1 libgomp1 libfontconfig1 \\" \
             "                   libxrender1 libxcursor1 libxft2 libxinerama1 \\" \
             "                   libxi6 libxext6" \
             "" \
             "  Fedora:" \
             "  sudo dnf install mesa-libGLU libglvnd-glx libgomp fontconfig \\" \
             "                   libXrender libXcursor libXft libXinerama \\" \
             "                   libXi libXext" \
             "" \
             "  Arch:" \
             "  sudo pacman -S --needed glu libglvnd gcc-libs fontconfig \\" \
             "                          libxrender libxcursor libxft libxinerama \\" \
             "                          libxi libxext" \
             "" \
             "The Fedora list is libglvnd-glx, not mesa-libGL: current Fedora" \
             "ships libGL.so.1 from libglvnd, and the obvious-looking guess does" \
             "not resolve." \
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
# directories, and before any rename can make it disappear. --update (the
# in-app updater's helper) additionally requires that an installation is there,
# so it can never create a fresh one or touch anything else.
if [ "$UPDATE" -eq 1 ] && [ ! -e "$TARGET/app/APP-MANIFEST.json" ]; then
    fail "--update: there is no Waveguide Generator installation at $TARGET." \
         "Nothing has been changed."
fi
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
# TARGET may resolve through a symlink; stage and back up beside its actual path.
TARGET_PARENT="${TARGET%/*}"
# Files are staged on the same filesystems as their final names, so the commit
# below consists only of renames and can restore every displaced predecessor.
STAGE_ROOT="$(mktemp -d "$TARGET_PARENT/.waveguide-generator.install.XXXXXX")" || \
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

# Swap table: LIVE[i], BACKUP[i], STAGED[i], with old/new inode identities.
# Each row goes staged -> prepared -> displace_intent -> displaced ->
# install_intent -> installed. Intent is recorded BEFORE acting; both the
# command result and the exact destination identity are verified afterward.
# Cleanup reconciles identities on disk even when a signal precedes bookkeeping:
# prepared/displace_intent keeps or restores old; displaced/install_intent/
# installed returns new to staging and restores old. Recovery goes through
# evacuate_intent -> evacuated (new back to stage), then
# restore_intent -> restored with the same verification for EVERY row.
# Only after all rows verify does COMMITTED retain new and retire backups.
# A raced destination is never deleted: report the real old-object location
# (including BSD mv nesting) with exit 3. Uncommitted clean recovery exits 1.
COMMITTED=0
LIVE=("$TARGET" "$APPLICATIONS/$DESKTOP_ENTRY_NAME" "$ICONS/$ICON_NAME"
      "$APPLICATIONS/$DESKTOP_OWNER_NAME" "$ICONS/$ICON_OWNER_NAME" "$BIN/$LAUNCHER_NAME")
STAGED=("$STAGED_TARGET" "$STAGED_DESKTOP" "$STAGED_ICON"
        "$STAGED_DESKTOP_OWNER" "$STAGED_ICON_OWNER" "$BIN/.waveguide-generator.link.new.$$")
BACKUP=("" "" "" "" "" "")
DESCRIPTION=("application" "desktop entry" "icon" "desktop ownership marker" "icon ownership marker" "command link")
INSTALL_ERROR=("Could not put the staged application in $TARGET." "Could not install the rendered desktop entry."
               "Could not install the application icon." "Could not install desktop ownership."
               "Could not install icon ownership." "Could not install the command link.")
STATE=(staged staged staged staged staged staged)
OLD_ID=("" "" "" "" "" "")
NEW_ID=("" "" "" "" "" "")
MV_OPTIONS=(-f --)

# Renames preserve the inode on this filesystem, including a broken symlink.
# Comparing identity, rather than existence/type, rejects both nesting and a
# plausible-looking directory created by another process.
object_id() {
    [ -e "$1" ] || [ -L "$1" ] || return 1
    identity=$(LC_ALL=C ls -di "$1" 2>/dev/null) || return 1
    identity=${identity#"${identity%%[! ]*}"}
    printf '%s\n' "${identity%% *}"
}
same_object() {
    [ -n "$2" ] && [ "$(object_id "$1")" = "$2" ]
}

# Catch signals in the parent; never ignore them in recovery children. Forward
# INT as TERM as well, because asynchronous POSIX jobs may start with INT ignored.
MOVE_PID=""
interrupt_recovery() {
    [ -z "$MOVE_PID" ] || kill -TERM "$MOVE_PID" 2>/dev/null || :
}
bounded_move() {
    (trap - HUP INT TERM; exec mv "${MV_OPTIONS[@]}" "$1" "$2" </dev/null) &
    MOVE_PID=$!
    (
        # A process-group signal must not cancel the deadline. It can shorten
        # it: an interrupted timer still kills the move. USR1 is our private
        # cancellation, sent only after the move has been reaped.
        trap ':' HUP INT TERM
        timer_pid=""
        trap '[ -z "$timer_pid" ] || kill "$timer_pid" 2>/dev/null || :; wait "$timer_pid" 2>/dev/null; exit' USR1
        sleep 5 &
        timer_pid=$!
        wait "$timer_pid" || :
        kill -KILL "$MOVE_PID" 2>/dev/null || :
        kill "$timer_pid" 2>/dev/null || :
        wait "$timer_pid" 2>/dev/null || :
    ) &
    watchdog_pid=$!
    move_status=1
    # A caught signal interrupts wait before the child exits. Reap that child
    # before inspecting paths, retrying, or stopping its watchdog.
    while :; do
        wait "$MOVE_PID"
        move_status=$?
        kill -0 "$MOVE_PID" 2>/dev/null || break
    done
    kill -USR1 "$watchdog_pid" 2>/dev/null || :
    wait "$watchdog_pid" 2>/dev/null || :
    MOVE_PID=""
    return "$move_status"
}

# Locate only the recorded object, never an unrelated occupant. BSD mv can nest
# at either end; these are the only locations a single rename can produce.
locate_old() {
    local candidate
    OLD_PATH=""
    for candidate in "${LIVE[i]}" "${BACKUP[i]}" \
                     "${BACKUP[i]}/${LIVE[i]##*/}" "${LIVE[i]}/${BACKUP[i]##*/}"; do
        if same_object "$candidate" "${OLD_ID[i]}"; then OLD_PATH="$candidate"; return 0; fi
    done
    return 1
}
locate_new() {
    local candidate
    NEW_PATH=""
    for candidate in "${STAGED[i]}" "${LIVE[i]}" "${LIVE[i]}/${STAGED[i]##*/}"; do
        if same_object "$candidate" "${NEW_ID[i]}"; then NEW_PATH="$candidate"; return 0; fi
    done
    return 1
}
verify_move() {
    ! { [ -e "$1" ] || [ -L "$1" ]; } && same_object "$2" "$3"
}
restore_row() {
    local attempt
    [ "${STATE[i]}" != staged ] || return 0
    # Put our new object back beside the target, rather than recursively deleting
    # a possibly foreign destination. A failed evacuation preserves old's backup.
    if locate_new && [ "$NEW_PATH" != "${STAGED[i]}" ]; then
        if [ -e "${STAGED[i]}" ] || [ -L "${STAGED[i]}" ]; then return 1; fi
        STATE[i]=evacuate_intent
        for attempt in 1 2; do
            bounded_move "$NEW_PATH" "${STAGED[i]}" || :
            verify_move "$NEW_PATH" "${STAGED[i]}" "${NEW_ID[i]}" && break
            locate_new || return 1
        done
        same_object "${STAGED[i]}" "${NEW_ID[i]}" || return 1
        STATE[i]=evacuated
    fi
    if [ -z "${OLD_ID[i]}" ]; then
        [ ! -e "${LIVE[i]}" ] && [ ! -L "${LIVE[i]}" ]
        return
    fi
    locate_old || return 1
    if [ "$OLD_PATH" = "${LIVE[i]}" ]; then return 0; fi
    [ ! -e "${LIVE[i]}" ] && [ ! -L "${LIVE[i]}" ] || return 1
    STATE[i]=restore_intent
    for attempt in 1 2; do
        bounded_move "$OLD_PATH" "${LIVE[i]}" || :
        if verify_move "$OLD_PATH" "${LIVE[i]}" "${OLD_ID[i]}"; then
            STATE[i]=restored
            return 0
        fi
        locate_old || return 1
        # A failed postcondition (including nesting) is not retried into a racer.
        [ ! -e "${LIVE[i]}" ] && [ ! -L "${LIVE[i]}" ] || return 1
    done
    return 1
}
rollback() {
    local status=$? i
    trap - EXIT
    trap 'interrupt_recovery' HUP INT TERM
    RESTORE_FAILED=0
    HAD_PREVIOUS=0
    if [ "$COMMITTED" -ne 1 ]; then
        status=1
        for ((i=0; i<${#LIVE[@]}; i++)); do
            [ -z "${OLD_ID[i]}" ] || HAD_PREVIOUS=1
            if ! restore_row; then
                printf 'ERROR: could not restore the previous %s.\n' "${DESCRIPTION[i]}" >&2
                if locate_old; then
                    printf 'Its backup remains at: %s\n' "$OLD_PATH" >&2
                else
                    printf 'ERROR: recorded previous object is missing from its recovery paths.\n' >&2
                fi
                RESTORE_FAILED=1
            fi
        done
        if [ "$RESTORE_FAILED" -ne 0 ]; then
            printf 'ERROR: rollback was incomplete; the backup paths above were preserved.\n' >&2
            status=3
        elif [ "$HAD_PREVIOUS" -eq 1 ]; then
            printf 'Restored the previous installation and desktop integration.\n'
        fi
    else
        status=0
    fi
    rm -rf -- "$STAGE_ROOT"
    rm -f -- "$STAGED_DESKTOP" "$STAGED_ICON" "$STAGED_DESKTOP_OWNER" "$STAGED_ICON_OWNER" "${STAGED[5]}"
    exit "$status"
}
trap rollback EXIT
trap 'exit 1' HUP INT TERM

# GNU mv prevents nesting atomically. BSD fixtures fall back to verified identity.
mkdir "$STAGE_ROOT/mv-probe-source" || fail "Could not probe safe rename support."
if (cd -- "$STAGE_ROOT" && mv -T -f -- mv-probe-source mv-probe-target </dev/null 2>/dev/null); then
    MV_OPTIONS=(-T -f --)
fi
rm -rf -- "$STAGE_ROOT/mv-probe-source" "$STAGE_ROOT/mv-probe-target"

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
        "Exec=@INSTALL_DIR@/$LAUNCHER_NAME")
            printf 'Exec="%s"\n' "$EXECUTABLE"
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

# Stage the link too, so every installation step below is the same rename.
[ ! -e "${STAGED[5]}" ] && [ ! -L "${STAGED[5]}" ] || fail "The staged command path is occupied."
ln -s -- "$TARGET/$LAUNCHER_NAME" "${STAGED[5]}" || fail "Could not stage the command link."

# Reserve same-directory rollback names, then leave them absent for renames.
BACKUP[0]="$(mktemp -d "$TARGET_PARENT/.waveguide-generator.previous.XXXXXX")" || fail "Could not reserve application rollback."
rmdir "${BACKUP[0]}" || fail "Could not prepare application rollback."
BACKUP[1]="$(mktemp "$APPLICATIONS/.waveguide-generator.desktop.backup.XXXXXX")" || fail "Could not prepare desktop rollback."
BACKUP[2]="$(mktemp "$ICONS/.waveguide-generator.icon.backup.XXXXXX")" || fail "Could not prepare icon rollback."
BACKUP[3]="$(mktemp "$APPLICATIONS/.waveguide-generator.owner.backup.XXXXXX")" || fail "Could not prepare desktop-owner rollback."
BACKUP[4]="$(mktemp "$ICONS/.waveguide-generator.owner.backup.XXXXXX")" || fail "Could not prepare icon-owner rollback."
BACKUP[5]="$(mktemp "$BIN/.waveguide-generator.link.backup.XXXXXX")" || fail "Could not prepare command rollback."
rm -f -- "${BACKUP[@]:1}"

printf 'Committing the staged installation ...\n'
for ((i=0; i<${#LIVE[@]}; i++)); do
    NEW_ID[i]="$(object_id "${STAGED[i]}")" || fail "The staged ${DESCRIPTION[i]} is missing."
    OLD_ID[i]="$(object_id "${LIVE[i]}")" || OLD_ID[i]=""
    STATE[i]=prepared
    if [ -n "${OLD_ID[i]}" ]; then
        [ ! -e "${BACKUP[i]}" ] && [ ! -L "${BACKUP[i]}" ] || fail "The backup path is still occupied."
        STATE[i]=displace_intent
        mv "${MV_OPTIONS[@]}" "${LIVE[i]}" "${BACKUP[i]}" </dev/null
        move_status=$?
        if ! verify_move "${LIVE[i]}" "${BACKUP[i]}" "${OLD_ID[i]}" || [ "$move_status" -ne 0 ]; then
            fail "Could not move the existing ${DESCRIPTION[i]} aside."
        fi
    fi
    STATE[i]=displaced
    [ ! -e "${LIVE[i]}" ] && [ ! -L "${LIVE[i]}" ] || fail "The ${DESCRIPTION[i]} destination is occupied."
    STATE[i]=install_intent
    mv "${MV_OPTIONS[@]}" "${STAGED[i]}" "${LIVE[i]}" </dev/null
    move_status=$?
    if ! verify_move "${STAGED[i]}" "${LIVE[i]}" "${NEW_ID[i]}" || [ "$move_status" -ne 0 ]; then
        fail "${INSTALL_ERROR[i]}"
    fi
    STATE[i]=installed
done
COMMITTED=1
rm -rf -- "${BACKUP[0]}"
rm -f -- "${BACKUP[@]:1}"

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
