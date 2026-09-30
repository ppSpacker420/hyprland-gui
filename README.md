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

## Keybindings

The **Action** dropdown lists every dispatcher your Hyprland exposes, each with
a one-line description. Pick one and the right argument control appears — a
dropdown of common values (`left`, `maximized`, `toggle`) where the set is
fixed, or a free-text box where it isn't. Choose "Run a command…" to get a
searchable list of everything this machine can run.

That list is built by scanning, never bundled — the set of usable commands
differs completely between distros, so a shipped list would be wrong
everywhere except the author's box. Three sources are combined:

1. every executable on `$PATH`
2. `Exec=` lines from `.desktop` entries, which is how Flatpak and Snap apps
   are installed and are otherwise invisible to a `PATH` scan
3. `alias` and `function` definitions from your shell rc files, which are
   runnable but are not files at all

Names carrying a dot are kept (`fsck.btrfs`, `alsa-info.sh`,
`gst-launch-1.0` are all real commands); what gets dropped is libraries, data
files, bare interpreters, and versioned twins like `aclocal-1.18` when plain
`aclocal` is also present. The result is cached for two hours under
`$XDG_CACHE_HOME/hypr-gui`, since the scan touches every directory on `PATH`.

Static and read-only: the rc files are parsed as text, nothing is evaluated,
and anything a shell generates at runtime is invisible.

### Recording a shortcut

The **Shortcut** field is a large press-to-record area, not a one-line text
field — pressing keys is the main interaction here, so it gets the space. Click
anywhere on it to arm it, press the combination, and it shows what it caught.

- a lone modifier shows as `SUPER + …` and keeps listening, so you can see it
  register before adding the key
- bare keys are allowed — `Q`, not just `SUPER + Q`
- `Backspace` clears and keeps listening, `Escape` cancels
- a text box underneath accepts `super+shift+r` or `super-r` typed by hand,
  normalised to `SUPER + SHIFT + R` with modifiers in a fixed order

The app also takes `--tab <name>`, so `hypr-gui --tab Keybindings` opens
straight to a tab and can be bound to a key.

Dispatcher names and argument keys were read out of the running compositor's
own `hl.dsp` tables, not copied from documentation. This matters: the 0.56+
Lua API namespaces most actions (`hl.dsp.window.close`, not `hl.dsp.close`),
and an earlier version of this app scraped from the wiki had every single name
wrong. The test suite probes all 50 against the live compositor.

## Tests

```sh
python3 test_hypr_gui.py      # config backends + live compositor
python3 test_dispatchers.py   # every dispatcher constructs
python3 test_ui.py            # builds the real GTK window
python3 test_scan.py          # command discovery
```

189 checks across four suites:

- **test_hypr_gui.py** — renders both the Lua and `.conf` backends into a temp
  directory and checks structure, injection, idempotency and the safety gates;
  then writes real changes to your compositor, verifies them via
  `hyprctl getoption`, exercises rollback, and restores your config
- **test_dispatchers.py** — probes all 50 dispatchers against the live
  compositor, and checks bindings actually register
- **test_ui.py** — constructs the real GTK window and exercises the
  dispatcher dropdown, the command search, the shortcut recorder (typed,
  recorded and cancelled) and the add-binding path, in a temp directory. It
  also runs itself in a child process and fails on any Gtk-CRITICAL, because a
  misused widget passes every assertion while still breaking the app
- **test_scan.py** — command discovery against synthetic `PATH`, `.desktop`
  and rc fixtures: which names are kept, which are dropped, and that launch
  wrappers (`env`, `nohup`, `flatpak`, `sh -c`) are unwrapped correctly

The live half skips itself with a message if no Hyprland session is reachable.
`test_ui.py` runs headless but does need a display for GTK to construct
widgets.

## Limitations

- The `.conf` backend is checked structurally, not parsed: Hyprland 0.56+ uses
  Lua and cannot parse `.conf` at all, so the author had no parser to test
  against. The legacy dispatcher names are marked `UNVERIFIED_CONF` in the
  source. If you run a pre-0.56 release, please run the test suite and open an
  issue if the live half fails — that path is the least proven part of this.
- `hl.dsp.window.fullscreen_state` is not offered: it rejects every argument
  form that could be constructed, so it appeared to be unusable.
- Options your Hyprland build doesn't have are greyed out rather than hidden,
  so you can see what exists on that version.
- Monitor scale is quantized by the compositor; hypr-gui re-reads the value
  that actually took effect after each reload.

## License

MIT
