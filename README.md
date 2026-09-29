# hypr-gui

A graphical settings editor for [Hyprland](https://hypr.land), the Wayland
compositor. Edit gaps, rounding, input, displays and keybindings with real
widgets instead of a text file.

## What it does

Seven tabs: **Look** (gaps, border, rounding, dim/blur/opacity, shadows,
animations), **Layout** (dwindle/master, snapping, scrolling width), **Input**
(pointer and touchpad), **Keyboard** (layouts, repeat rate, numlock),
**Displays** (resolution, rotation, scale, position, VRR), **Keybindings**
(add your own with a key recorder, unbind inherited ones), and **Config files**
(read and edit your configs directly).

Every control starts from the value the compositor is actually using, so the
GUI never disagrees with your running session.

## How it saves

hypr-gui never edits your existing config. It writes only the settings you
changed into a single generated file, which is loaded last:

| Hyprland config | generated file | injected with |
|---|---|---|
| `hyprland.lua` (0.56+) | `~/.config/hypr/hyprgui.lua` | `require("hypr.hyprgui")` |
| `hyprland.conf` (older) | `~/.config/hypr/hyprgui.conf` | `source = ~/.config/hypr/hyprgui.conf` |

Because the injection is added at the end, your settings win over whatever came
before, and package updates that rewrite your config's defaults won't clobber
anything. Changing a value back to what the compositor reports removes it from
the generated file again, so it stays as small as your actual changes.

Your GUI state lives in `~/.config/hypr/.hypr-gui-state.json`. Deleting that
plus the generated file resets everything the GUI has touched.

## Safety

Writing a broken Hyprland config locks you out of your window manager, so:

1. Generated output is parsed (`luac -p` for Lua, a structural check for
   `.conf`) **before** it is written. Invalid output is never saved.
2. After every reload hypr-gui checks `hyprctl configerrors`. If the compositor
   rejected something, the last known-good file is restored and reloaded, and
   the error is shown to you.

## Install

```sh
git clone https://github.com/you/hypr-gui
cd hypr-gui
./install.sh
hypr-gui
```

`install.sh` copies the app to `~/.local/share/hypr-gui/`, creates a
`hypr-gui` launcher in `~/.local/bin/`, and registers a desktop entry.

### Requirements

- Hyprland (0.46+; both the Lua and `.conf` config formats are supported)
- Python 3.9+
- GTK 4, libadwaita, PyGObject
- `luac` (optional — Lua installs skip the syntax check without it)

```sh
# Arch
sudo pacman -S python-gobject gtk4 libadwaita
# Debian / Ubuntu
sudo apt install python3-gi gir1.2-adw-1 libadwaita-1-0
# Fedora
sudo dnf install python3-gobject gtk4 libadwaita
```

The launcher looks for a `python3` that actually has PyGObject, since distro
packages install into the system interpreter rather than whatever version a
pyenv/uv/conda shim is first on your `PATH`.

## Tests

```sh
python3 test_hypr_gui.py
```

57 checks, in two halves:

- **offline** — renders both the Lua and `.conf` backends into a temp
  directory and checks structure, injection, idempotency and the safety gates
- **live** — writes real changes to your running compositor, verifies them via
  `hyprctl getoption`, exercises the rollback path, then restores your config

The live half skips itself with a message if no Hyprland session is reachable.

## Limitations

- The `.conf` backend is checked structurally, not parsed: a Hyprland new
  enough to run this app uses Lua and cannot parse `.conf` at all. Run the
  tests on a pre-0.56 machine to exercise it end to end.
- Options your Hyprland build doesn't have are greyed out rather than hidden,
  so you can see what exists on that version.
- Monitor scale is quantized by the compositor; hypr-gui re-reads the value
  that actually took effect after each reload.

## License

MIT
