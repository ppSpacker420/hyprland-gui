#!/bin/bash
# Install hypr-gui into ~/.local. No root, no system files touched.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
APPS="$PREFIX/applications"

say() { printf '  %s\n' "$*"; }

echo "Installing hypr-gui"

# --- preflight -----------------------------------------------------------
if ! command -v hyprctl >/dev/null 2>&1; then
  say "WARN  hyprctl not found - is Hyprland installed?"
fi

# Find a python3 that can actually import PyGObject.
PY=""
for candidate in /usr/bin/python3 /usr/local/bin/python3 python3; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1")' >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done

if [ -z "$PY" ]; then
  say "ERROR no python3 with PyGObject + GTK4 + libadwaita found."
  say ""
  say "  Arch:   sudo pacman -S python-gobject gtk4 libadwaita"
  say "  Debian: sudo apt install python3-gi gir1.2-adw-1 libadwaita-1-0"
  say "  Fedora: sudo dnf install python3-gobject gtk4 libadwaita"
  exit 1
fi
say "Using $PY ($("$PY" --version 2>&1))"

if ! command -v luac >/dev/null 2>&1; then
  say "WARN  luac not found - Lua configs will be written without a syntax pre-check"
fi

# --- install -------------------------------------------------------------
mkdir -p "$PREFIX/hypr-gui" "$BIN" "$APPS"
install -m 644 "$SRC/hypr_gui.py"   "$PREFIX/hypr-gui/hypr_gui.py"
install -m 644 "$SRC/test_hypr_gui.py" "$PREFIX/hypr-gui/test_hypr_gui.py"
install -m 644 "$SRC/README.md"     "$PREFIX/hypr-gui/README.md"
if [ -f "$SRC/LICENSE" ]; then
  install -m 644 "$SRC/LICENSE" "$PREFIX/hypr-gui/LICENSE"
fi

cat > "$BIN/hypr-gui" <<EOF
#!/bin/bash
# Launch hypr-gui on a Python that actually has PyGObject.
# Distro packages (python-gobject) install into the system interpreter, not
# into whatever version a pyenv/uv/conda shim happens to be first on \$PATH.
APP="\${XDG_DATA_HOME:-\$HOME/.local/share}/hypr-gui/hypr_gui.py"
for py in /usr/bin/python3 /usr/local/bin/python3 python3; do
  command -v "\$py" >/dev/null 2>&1 || continue
  if "\$py" -c 'import gi' >/dev/null 2>&1; then
    exec "\$py" "\$APP" "\$@"
  fi
done

echo "hypr-gui: no python3 with PyGObject found." >&2
echo "Install it, e.g.:" >&2
echo "  Arch:   sudo pacman -S python-gobject gtk4 libadwaita" >&2
echo "  Debian: sudo apt install python3-gi gir1.2-adw-1 libadwaita-1-0" >&2
echo "  Fedora: sudo dnf install python3-gobject gtk4 libadwaita" >&2
exit 1
EOF
chmod +x "$BIN/hypr-gui"
say "Installed launcher: $BIN/hypr-gui"

sed "s|^Exec=.*|Exec=$BIN/hypr-gui|" hypr-gui.desktop > "$APPS/hypr-gui.desktop"
say "Installed desktop entry: $APPS/hypr-gui.desktop"

command -v update-desktop-database >/dev/null 2>&1 && \
  update-desktop-database "$APPS" 2>/dev/null || true

# --- done ----------------------------------------------------------------
echo ""
echo "Done. Start it with:  hypr-gui"
echo "Your config is untouched until you change something in the GUI."
exit 0
