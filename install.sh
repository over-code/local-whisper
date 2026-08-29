#!/usr/bin/env bash
#
# Installer for Local Whisper on Debian/Ubuntu with KDE Plasma.
#
# It is deliberately boring: a virtualenv under ~/.local/share, a symlink in
# ~/.local/bin, a desktop file, and — only if you say yes — a systemd user
# service, the KDE shortcuts, and the input-group permissions that hold-to-talk
# needs. Everything it does is printed before it happens.
#
# Usage:
#   ./install.sh                 interactive install
#   ./install.sh --yes           accept every optional step
#   ./install.sh --minimal       skip service, shortcuts and permissions
#   ./install.sh --uninstall     remove everything it installed

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/local-whisper"
VENV="$PREFIX/venv"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

ASSUME_YES=0
MINIMAL=0
UNINSTALL=0

for arg in "$@"; do
    case "$arg" in
        -y|--yes)     ASSUME_YES=1 ;;
        --minimal)    MINIMAL=1 ;;
        --uninstall)  UNINSTALL=1 ;;
        -h|--help)    sed -n '2,20p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; OFF=$'\033[0m'
say()  { printf '%s==>%s %s\n' "$BOLD" "$OFF" "$*"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$OFF" "$*"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$OFF" "$*"; }
die()  { printf '  %s✗%s %s\n' "$RED" "$OFF" "$*" >&2; exit 1; }

ask() {
    # ask "question" -> 0 for yes
    [ "$ASSUME_YES" = 1 ] && return 0
    [ "$MINIMAL" = 1 ] && return 1
    local reply
    read -r -p "  ${BOLD}?${OFF} $1 [y/N] " reply </dev/tty || return 1
    [[ "$reply" =~ ^[Yy] ]]
}

# --------------------------------------------------------------- uninstall

if [ "$UNINSTALL" = 1 ]; then
    say "Removing Local Whisper"
    systemctl --user disable --now local-whisper.service 2>/dev/null && ok "stopped the service" || true
    rm -f "$UNIT_DIR/local-whisper.service" "$BIN_DIR/local-whisper"
    rm -f "$DESKTOP_DIR/local-whisper.desktop" "$ICON_DIR/local-whisper.svg"
    "$VENV/bin/local-whisper" remove-shortcut >/dev/null 2>&1 || true
    rm -rf "$VENV"
    ok "removed the virtualenv, launcher and desktop entry"
    warn "kept your settings and history: ${XDG_CONFIG_HOME:-$HOME/.config}/local-whisper, ${XDG_DATA_HOME:-$HOME/.local/share}/local-whisper"
    warn "models stay cached in ${XDG_CACHE_HOME:-$HOME/.cache}/local-whisper"
    exit 0
fi

# ------------------------------------------------------------ prerequisites

say "Checking prerequisites"
command -v python3 >/dev/null || die "python3 is not installed"
PYTHON_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "Python 3.10 or newer is required (found $PYTHON_VERSION)"
ok "python $PYTHON_VERSION"

SESSION_TYPE="${XDG_SESSION_TYPE:-unknown}"
ok "session: ${XDG_CURRENT_DESKTOP:-unknown desktop} / $SESSION_TYPE"

APT_PACKAGES=(python3-venv python3-dev libportaudio2)
if [ "$SESSION_TYPE" = "wayland" ]; then
    APT_PACKAGES+=(wl-clipboard wtype ydotool)
else
    APT_PACKAGES+=(xdotool xclip)
fi

MISSING=()
for package in "${APT_PACKAGES[@]}"; do
    dpkg -s "$package" >/dev/null 2>&1 || MISSING+=("$package")
done

if [ ${#MISSING[@]} -gt 0 ]; then
    warn "missing system packages: ${MISSING[*]}"
    if ask "install them with sudo apt install?"; then
        sudo apt-get update
        sudo apt-get install -y "${MISSING[@]}" || warn "some packages could not be installed; continuing"
    else
        warn "continuing without them — dictation may not be able to insert text"
    fi
else
    ok "system packages are present"
fi

# ------------------------------------------------------------------ install

say "Installing into $VENV"
mkdir -p "$PREFIX" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip wheel
"$VENV/bin/pip" install --quiet "$REPO_DIR[hotkey]"
ok "installed $("$VENV/bin/local-whisper" --version)"

ln -sf "$VENV/bin/local-whisper" "$BIN_DIR/local-whisper"
ok "linked $BIN_DIR/local-whisper"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH — add it in ~/.profile" ;;
esac

install -m 0644 "$REPO_DIR/packaging/icons/local-whisper.svg" "$ICON_DIR/local-whisper.svg"
sed "s|^Exec=local-whisper|Exec=$BIN_DIR/local-whisper|" \
    "$REPO_DIR/packaging/local-whisper.desktop" > "$DESKTOP_DIR/local-whisper.desktop"
chmod 0644 "$DESKTOP_DIR/local-whisper.desktop"
ok "installed the desktop entry and icon"
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

# ------------------------------------------------------------ optional bits

say "Optional setup"

if ask "start Local Whisper automatically at login (systemd user service)?"; then
    mkdir -p "$UNIT_DIR"
    sed "s|%h/.local/bin/local-whisper|$BIN_DIR/local-whisper|" \
        "$REPO_DIR/packaging/systemd/local-whisper.service" > "$UNIT_DIR/local-whisper.service"
    systemctl --user daemon-reload
    systemctl --user enable --now local-whisper.service && ok "service enabled and started" \
        || warn "could not start the service — run: systemctl --user status local-whisper"
else
    warn "skipped autostart — run 'local-whisper daemon' when you want it"
fi

if ask "register the KDE global shortcuts (Meta+Alt+D to dictate)?"; then
    "$BIN_DIR/local-whisper" install-shortcut || warn "shortcut registration reported problems"
fi

if ask "enable hold-to-talk and built-in typing (adds you to the 'input' group)?"; then
    sudo cp "$REPO_DIR/packaging/udev/99-local-whisper-uinput.rules" /etc/udev/rules.d/
    sudo udevadm control --reload-rules && sudo udevadm trigger || true
    sudo modprobe uinput || warn "could not load the uinput module"
    echo uinput | sudo tee /etc/modules-load.d/uinput.conf >/dev/null
    sudo usermod -aG input "$USER"
    ok "permissions installed — log out and back in for the group change to apply"
    warn "note: this lets any program running as you read the keyboard and type"
fi

# ------------------------------------------------------------------- finish

say "Checking the installation"
"$BIN_DIR/local-whisper" doctor || true

cat <<EOF

${BOLD}Local Whisper is installed.${OFF}

  Start it            : local-whisper daemon      ${DIM}(or it is already running as a service)${OFF}
  Dictate             : press Meta+Alt+D, speak, press it again
  Settings            : local-whisper settings    ${DIM}(or click the tray microphone)${OFF}
  Check the setup     : local-whisper doctor

The first dictation downloads the Whisper model (~480 MB for 'small'); after
that nothing leaves your machine.
EOF
