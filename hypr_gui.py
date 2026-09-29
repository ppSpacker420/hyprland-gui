#!/usr/bin/python3
"""hypr-gui — a graphical settings editor for this machine's Hyprland (Lua config).

Reads live values from the compositor, lets you change them, and writes your
changes into ~/.config/hypr/hyprgui.lua, which is generated from
~/.config/hypr/.hypr-gui-state.json. Only settings you actually touch are
written, so Omarchy defaults and your hand-written files keep working.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

HYPR_DIR = Path.home() / ".config" / "hypr"
STATE_FILE = HYPR_DIR / ".hypr-gui-state.json"

APP_CLASS = "io.github.ppSpacker420.hyprgui"

# Hyprland 0.56 replaced hyprland.conf with a Lua config. Both are supported;
# which one is in use decides the file we generate and how we inject it.
LUA_MAIN = HYPR_DIR / "hyprland.lua"
CONF_MAIN = HYPR_DIR / "hyprland.conf"

# hyprland.conf comments start with #, Lua comments with --. The fence names
# are shared; only the comment sigil differs.
MARKS = {
    "lua": ("-- [[ HYPRGUI_START ]]", "-- [[ HYPRGUI_END ]]"),
    "conf": ("# [[ HYPRGUI_START ]]", "# [[ HYPRGUI_END ]]"),
}
MARK_START, MARK_END = MARKS["lua"]

class LuaSyntaxError(RuntimeError):
    """The generated config would not parse; never written to disk."""


LAYOUTS = ["dwindle", "master"]
ACCEL = ["adaptive", "flat", "custom"]


def run(*args, check=False):
    p = subprocess.run(args, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or " ".join(args))
    return p.stdout.strip()


def hypr(*args):
    return run("hyprctl", *args)


def hypr_json(*args):
    try:
        return json.loads(hypr("-j", *args) or "null")
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# dispatchers and commands
# --------------------------------------------------------------------------

# Taken from https://wiki.hypr.land/configuring/core/dispatchers/ (Lua config,
# Hyprland 0.56+). On the older .conf format the same actions are spelled
# without hl.dsp, e.g. "killactive" rather than hl.dsp.close().
# The Lua API (Hyprland 0.56+) namespaces most actions: hl.dsp.window.close(),
# not hl.dsp.close(). The names and arg keys below were read out of the running
# compositor's own hl.dsp tables, not from documentation, so they match reality
# rather than a wiki page that may be ahead of or behind the installed version.
#
#   (display name, lua call path, arg key or None, description)
# Each entry: (display name, lua call path, arg key, plain-string?, description)
#
# The names, arg keys and calling conventions below were read out of the running
# compositor by probing hl.dsp directly, not copied from documentation: the
# 0.56+ Lua API namespaces most actions (hl.dsp.window.close, not
# hl.dsp.close), and six of them take a bare string rather than a table.
# plain=True means call it as hl.dsp.foo("value") instead of foo({ key = "value" }).
DISPATCHERS = [
    # --- window
    ("close", "window.close", None, False, "Close the focused window"),
    ("kill", "window.kill", None, False, "Kill the window's process with SIGKILL"),
    ("fullscreen", "window.fullscreen", "mode", False, "Fullscreen: maximized, fullscreen or none"),
    ("float", "window.float", "action", False, "Float a window: toggle, enable or disable"),
    ("move", "window.move", "direction", False, "Move the window: left, right, up or down"),
    ("resize", "window.resize", "x", False, "Resize the window by a pixel amount"),
    ("swap", "window.swap", "direction", False, "Swap the window with its neighbour"),
    ("pin", "window.pin", "window", False, "Pin the window to every workspace"),
    ("center", "window.center", None, False, "Center the window on screen"),
    ("cycle next", "window.cycle_next", None, False, "Focus the next window"),
    ("bring to top", "window.bring_to_top", None, False, "Raise the window above others"),
    ("alter zorder", "window.alter_zorder", "mode", False, "Send the window to top or bottom"),
    ("clear tags", "window.clear_tags", "window", False, "Clear the window's tags"),
    ("drag", "window.drag", None, False, "Begin an interactive drag (mouse binds)"),
    ("pseudo", "window.pseudo", "toggle", False, "Toggle pseudotiling for the window"),
    ("show swallowed", "window.toggle_swallow", None, False, "Toggle swallowed windows visible"),
    ("set prop", "window.set_prop", "prop", False, "Set a window property, e.g. alpha"),
    ("signal", "window.signal", "signal", False, "Send a POSIX signal to the window"),
    ("tag", "window.tag", "tag", False, "Tag the window"),
    ("deny from group", "window.deny_from_group", "window", False, "Keep the window out of a group"),
    # --- workspace
    ("move to workspace", "workspace.move", "workspace", False, "Move the window to a workspace"),
    ("rename workspace", "workspace.rename", "name", False, "Rename a workspace"),
    ("change workspace id", "workspace.change_id", "id", False, "Renumber the current workspace"),
    ("special workspace", "workspace.toggle_special", "name", False, "Toggle a special workspace"),
    ("swap monitors", "workspace.swap_monitors", "monitor1", False, "Swap two monitors' workspaces"),
    # --- focus
    ("focus", "focus", "direction", False, "Move focus: left, right, up, down or last"),
    # --- group
    ("group: toggle", "group.toggle", None, False, "Toggle grouping on the focused window"),
    ("group: next", "group.next", None, False, "Switch to the next window in the group"),
    ("group: prev", "group.prev", None, False, "Switch to the previous window in the group"),
    ("group: active", "group.active", "index", False, "Switch to a window in the group by index"),
    ("group: lock", "group.lock", None, False, "Lock the group"),
    ("group: lock active", "group.lock_active", None, False, "Lock the active group"),
    ("group: move window", "group.move_window", "direction", False, "Move a window within the group"),
    # --- cursor
    ("cursor: move", "cursor.move", "x", False, "Move the cursor to an x coordinate"),
    ("cursor: corner", "cursor.move_to_corner", "corner", False, "Move the cursor to a window corner [0-3]"),
    # --- plain-string dispatchers: called as hl.dsp.foo("value")
    ("exec", "exec_cmd", None, True, "Run a shell command"),
    ("exec raw", "exec_raw", None, True, "Run a command without a shell"),
    ("layout message", "layout", "value", True, "Send a layout message, e.g. master"),
    ("submap", "submap", "value", True, "Move to a submap"),
    ("global shortcut", "global", "value", True, "Activate a D-Bus global shortcut"),
    ("event", "event", "value", True, "Send an event to socket2"),
    # --- numeric
    ("force idle", "force_idle", "seconds", True, "Force idle timers (idle only, not keybinds)"),
    # --- misc
    ("dpms", "dpms", None, False, "Toggle monitors (idle only, not keybinds)"),
    ("force renderer reload", "force_renderer_reload", None, False, "Force reload the renderer"),
    ("release input capture", "release_input_capture", None, False, "End an input capture session"),
    ("send shortcut", "send_shortcut", "key", False, "Forward a shortcut to the focused window"),
    ("send key state", "send_key_state", "state", False, "Send a key with explicit down/up state"),
    ("pass", "pass", "window", False, "Let the shortcut pass through to the window"),
    ("no-op", "no_op", None, False, "Do nothing; useful for conditional binds"),
    ("exit Hyprland", "exit", None, False, "Quit Hyprland (prefer hyprshutdown)"),
]

# hyprland.conf spelling for the same actions, for pre-0.56 configs. Those use a
# flat name, e.g. "closewindow" rather than hl.dsp.window.close().
#
# Caveat: the names below are the documented legacy spellings. A Hyprland new
# enough to run this app has removed the legacy parser entirely, so they cannot
# be verified from a 0.56+ install - only a pre-0.56 machine can confirm them.
# UNVERIFIED_CONF marks the ones that are most likely to have drifted.
UNVERIFIED_CONF = {
    "window.pin", "window.clear_tags", "window.pseudo", "window.signal",
    "window.tag", "window.deny_from_group", "group.next", "group.prev",
    "workspace.change_id", "workspace.swap_monitors", "global",
}
CONF_DISPATCHERS = {
    "window.close": "closewindow", "window.kill": "killactive",
    "window.fullscreen": "fullscreen", "window.float": "togglefloating",
    "window.move": "movewindow", "window.resize": "resizeactive",
    "window.swap": "swapwindow", "window.pin": "pin",
    "window.center": "centerwindow", "window.cycle_next": "cyclenext",
    "window.bring_to_top": "bringactivetotop",
    "window.alter_zorder": "alterzorder", "window.clear_tags": "cleartags",
    "window.drag": "beginmove", "window.pseudo": "pseudo",
    "window.toggle_swallow": "toggleswallow", "window.set_prop": "setprop",
    "window.signal": "signalwindow", "window.tag": "tagwindow",
    "window.deny_from_group": "denywindowfromgroup", "window.pin": "pin",
    "window.clear_tags": "cleartags",
    "workspace.move": "movetoworkspace", "workspace.rename": "renameworkspace",
    "workspace.change_id": "changeactiveworkspace",
    "workspace.toggle_special": "togglespecialworkspace",
    "workspace.swap_monitors": "swapmonitors",
    "focus": "focuswindow",
    "group.toggle": "togglegroup", "group.next": "nextfocusedgroup",
    "group.prev": "prevfocusedgroup", "group.active": "activewindow",
    "group.lock": "lockgroups", "group.lock_active": "lockgroups",
    "group.move_window": "movewindow",
    "cursor.move": "movecursor", "cursor.move_to_corner": "movecursortocorner",
    "exec_cmd": "exec", "exec_raw": "exec", "layout": "layout", "submap": "submap",
    "send_shortcut": "sendshortcut", "send_key_state": "sendkeystate",
    "global": "globalshortcut", "event": "event", "dpms": "dpms",
    "force_idle": "forceidle",
    "force_renderer_reload": "forcerendererreload",
    "release_input_capture": "releaseinputcapture", "pass": "pass",
    "no_op": "pass", "exit": "exit",
}

# Arguments the GUI offers as one click, where the set is small and fixed.
# Keyed by the DISPATCHERS display name. A dispatcher with an arg key but no
# entry here would emit a bare hl.dsp.foo() and be rejected by the compositor,
# so the test suite asserts every one is present.
ARG_SUGGESTIONS = {
    "focus": ["left", "right", "up", "down", "last", "urgent_or_last", "e-1", "e+1"],
    "move": ["left", "right", "up", "down"],
    "swap": ["left", "right", "up", "down"],
    "fullscreen": ["maximized", "fullscreen", "none"],
    "float": ["toggle", "enable", "disable"],
    "resize": ["40", "80", "120", "200"],
    "alter zorder": ["top", "bottom"],
    "pin": ["activewindow"],
    "clear tags": ["activewindow"],
    "deny from group": ["activewindow"],
    "pseudo": ["true", "false"],
    "pass": ["activewindow"],
    "set prop": ["alpha", "no_border", "noblur"],
    "signal": ["15", "1", "9"],
    "tag": ["1", "2", "3"],
    "move to workspace": ["1", "2", "3", "4", "5", "e-1", "e+1"],
    "rename workspace": ["scratch", "web", "code"],
    "change workspace id": ["1", "2", "3", "4", "5"],
    "special workspace": ["magic", "scratch"],
    "swap monitors": ["HDMI-A-1", "eDP-1"],
    "group: active": ["1", "2", "3", "4"],
    "group: move window": ["left", "right", "up", "down"],
    "cursor: move": ["0", "500", "1000"],
    "cursor: corner": ["0", "1", "2", "3"],
    "layout message": ["master", "dwindle", "monocle"],
    "submap": ["scratch"],
    "global shortcut": ["__default"],
    "event": ["x"],
    "force idle": ["1", "60", "300"],
    "send shortcut": ["a", "Return", "Tab"],
    "send key state": ["down", "up"],
}

# Numeric arguments: written as a Lua number, not a quoted string.
NUMERIC_ARGS = {"x", "corner", "group", "seconds", "id", "y", "dx", "dy"}

# Several dispatchers require more keys than the single argument the GUI asks
# for. Rather than hide them, we supply a default for the companion key and let
# the user set the one that matters. Verified against the running compositor:
# each of these errors with "X is required" when its companion is missing.
COMPANION_ARGS = {
    "window.resize": {"y": "0"},
    "pass": {},
    "window.set_prop": {"value": '"1"'},
    "workspace.move": {"monitor": '""'},
    "workspace.rename": {"workspace": '""'},
    "workspace.change_id": {"workspace": '""'},
    "workspace.swap_monitors": {"monitor2": '""'},
    "cursor.move": {"y": "0"},
    "send_shortcut": {"mods": '""'},
    "send_key_state": {"mods": '""', "key": '"a"'},
    "force_idle": {},
}

# Suffixes that mark a command as a screenshot/audio/launch helper rather than
# something a user would bind to a bare key.
_CMD_SKIP_SUFFIX = (".so", ".pyc", ".png", ".jpg", ".svg", ".desktop", ".1", ".2")


def scan_commands():
    """Every executable on this machine's PATH that looks bindable.

    Scanned rather than bundled: the useful list is exactly what this user can
    actually run, and it differs per machine.
    """
    found = set()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory or not os.path.isdir(directory):
            continue
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for name in entries:
            if name.startswith(".") or name.endswith(_CMD_SKIP_SUFFIX):
                continue
            path = os.path.join(directory, name)
            if not os.access(path, os.X_OK) or not os.path.isfile(path):
                continue
            # Skip anything with an extension: those are libraries, not commands.
            if "." in name:
                continue
            found.add(name)
    return sorted(found)


# --------------------------------------------------------------------------
# live value reading
# --------------------------------------------------------------------------

_UNSUPPORTED = set()


def unsupported_options():
    """Keys this Hyprland build doesn't recognise.

    Option names move between releases, so probing once at startup keeps the
    GUI from writing settings that can only ever fail to apply.
    """
    if not _UNSUPPORTED:
        for specs in SCHEMA.values():
            for spec in specs:
                if hypr("getoption", spec["key"]).startswith("no such option"):
                    _UNSUPPORTED.add(spec["key"])
    return _UNSUPPORTED


def get_option(key):
    """Return the current value of a Hyprland option, or None if unknown."""
    out = hypr("getoption", key)
    first = out.splitlines()[0] if out else ""
    if not first or "no such option" in first:
        return None
    if first.startswith("bool:"):
        return first[5:].strip() == "true"
    if first.startswith("int:"):
        try:
            return int(float(first[4:]))
        except ValueError:
            return None
    if first.startswith("float:"):
        try:
            return round(float(first[6:]), 3)
        except ValueError:
            return None
    if first.startswith("str:"):
        return first[4:].strip()
    if first.startswith("css gap data:"):
        try:
            return int(float(first.split()[3]))
        except (ValueError, IndexError):
            return None
    return None


# --------------------------------------------------------------------------
# schema: key -> control description
#   type: bool | int | float | str | enum
# --------------------------------------------------------------------------

def S(key, label, type_, section, **kw):
    d = {"key": key, "label": label, "type": type_, "section": section}
    d.update(kw)
    return d


SCHEMA = {
    "look": [
        S("general:gaps_in", "Gaps between windows", "int", "Look & Feel", lo=0, hi=200),
        S("general:gaps_out", "Gaps around the screen edge", "int", "Look & Feel", lo=0, hi=200),
        S("general:float_gaps", "Gaps around floating windows", "int", "Look & Feel", lo=0, hi=200),
        S("general:border_size", "Window border thickness", "int", "Look & Feel", lo=0, hi=20),
        S("decoration:rounding", "Corner rounding", "int", "Look & Feel", lo=0, hi=40),
        S("decoration:border_part_of_window", "Border is part of the window", "bool", "Look & Feel"),
        S("decoration:dim_inactive", "Dim unfocused windows", "bool", "Look & Feel"),
        S("decoration:dim_strength", "Dim strength", "float", "Look & Feel", lo=0.0, hi=1.0, step=0.05),
        S("decoration:active_opacity", "Focused window opacity", "float", "Look & Feel", lo=0.0, hi=1.0, step=0.05),
        S("decoration:inactive_opacity", "Unfocused window opacity", "float", "Look & Feel", lo=0.0, hi=1.0, step=0.05),
        S("decoration:blur:enabled", "Blur behind windows", "bool", "Look & Feel"),
        S("decoration:blur:size", "Blur strength", "int", "Look & Feel", lo=0, hi=100, deps="decoration:blur:enabled"),
        S("decoration:shadow:enabled", "Window shadows", "bool", "Look & Feel"),
        S("animations:enabled", "Animations", "bool", "Look & Feel"),
    ],
    "layout": [
        S("general:layout", "Default layout", "enum", "Layout", options=LAYOUTS),
        S("general:snap:enabled", "Snap windows to edges", "bool", "Layout"),
        S("general:resize_on_border", "Resize by dragging window border", "bool", "Layout"),
        S("general:extend_border_grab_area", "Border grab area", "int", "Layout", lo=0, hi=50),
        S("scrolling:column_width", "Scrolling layout column width", "float", "Layout", lo=0.1, hi=1.0, step=0.01),
    ],
    "mouse": [
        S("input:sensitivity", "Pointer speed", "float", "Mouse & Touchpad", lo=-2.0, hi=2.0, step=0.01),
        S("input:accel_profile", "Acceleration profile", "enum", "Mouse & Touchpad", options=ACCEL),
        S("input:follow_mouse", "Focus follows mouse", "bool", "Mouse & Touchpad"),
        S("input:natural_scroll", "Natural scrolling (mouse)", "bool", "Mouse & Touchpad"),
        S("input:left_handed", "Left-handed mouse", "bool", "Mouse & Touchpad"),
        S("input:scroll_factor", "Scroll speed (mouse)", "float", "Mouse & Touchpad", lo=0.1, hi=4.0, step=0.05),
        S("input:mouse_refocus", "Refocus window under cursor", "bool", "Mouse & Touchpad"),
        S("input:touchpad:natural_scroll", "Natural scrolling (touchpad)", "bool", "Mouse & Touchpad"),
        S("input:touchpad:clickfinger_behavior", "Two-finger right click", "bool", "Mouse & Touchpad"),
        S("input:touchpad:tap_to_click", "Tap to click", "bool", "Mouse & Touchpad"),
        S("input:touchpad:disable_while_typing", "Disable while typing", "bool", "Mouse & Touchpad"),
        S("input:touchpad:scroll_factor", "Scroll speed (touchpad)", "float", "Mouse & Touchpad", lo=0.1, hi=4.0, step=0.05),
        S("input:touchpad:drag_3fg", "Three-finger drag", "int", "Mouse & Touchpad", lo=0, hi=1),
    ],
    "keyboard": [
        S("input:kb_layout", "Layouts (comma separated)", "str", "Keyboard",
          placeholder="us,dk,eu"),
        S("input:kb_variant", "Layout variant", "str", "Keyboard", placeholder="intl"),
        S("input:kb_model", "Keyboard model", "str", "Keyboard", placeholder="pc105"),
        S("input:repeat_rate", "Key repeat rate", "int", "Keyboard", lo=1, hi=100),
        S("input:repeat_delay", "Key repeat delay (ms)", "int", "Keyboard", lo=100, hi=2000, step=10),
        S("input:numlock_by_default", "Numlock on at startup", "bool", "Keyboard"),
    ],
}


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"options": {}, "monitors": {}, "binds": [], "unbinds": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


# --------------------------------------------------------------------------
# Lua generation
# --------------------------------------------------------------------------

def lua_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def lua_path(key):
    return key.split(":")


def emit_table(node, indent):
    pad = "  " * indent
    inner = "  " * (indent + 1)
    out = ["{"]
    for k, v in node.items():
        if isinstance(v, dict):
            out.append(f"{inner}{k} = {emit_table(v, indent + 1)},")
        else:
            out.append(f"{inner}{k} = {lua_value(v)},")
    out.append(pad + "}")
    return "\n".join(out)


def build_config_tree(state):
    tree = {}
    for key, val in state.get("options", {}).items():
        node = tree
        parts = lua_path(key)
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = val
    return tree


def config_style():
    """Return "lua" or "conf" for the config this Hyprland actually loads.

    0.56+ is Lua. Older releases - and plenty of current distros - use
    hyprland.conf. We go by what is on disk, not by a version number.
    """
    if LUA_MAIN.exists():
        return "lua"
    if CONF_MAIN.exists():
        return "conf"
    return "conf"


def gen_file(style=None):
    style = style or config_style()
    return HYPR_DIR / ("hyprgui.lua" if style == "lua" else "hyprgui.conf")


def main_file(style=None):
    style = style or config_style()
    return LUA_MAIN if style == "lua" else CONF_MAIN


def conf_value(v):
    """hyprland.conf syntax: booleans and numbers are bare, strings are bare."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def emit_conf(state):
    """Render the user's changes as hyprland.conf statements.

    Option keys are paths like input.touchpad.natural_scroll, which Hyprland
    wants as nested blocks with bare values.
    """
    start, end = MARKS["conf"]
    out = [
        start,
        "#   Written by hypr-gui. Only the settings you changed in the GUI live",
        "#   here; everything else stays under your own config's control.",
        "#   Safe to hand-edit, but the GUI rewrites everything between the marks.",
    ]

    def render(node, path, depth):
        pad = "  " * depth
        blocks = {k: v for k, v in node.items() if isinstance(v, dict)}
        scalars = {k: v for k, v in node.items() if not isinstance(v, dict)}
        if not blocks and not scalars:
            return []
        lines = [f"{pad}{path} {{"]
        for k, v in scalars.items():
            lines.append(f"{pad}  {k} = {conf_value(v)}")
        for k, v in blocks.items():
            lines.extend(render(v, k, depth + 1))
        lines.append(f"{pad}}}")
        return lines

    for key, value in build_config_tree(state).items():
        if isinstance(value, dict):
            out.extend(render(value, key, 0))
        else:
            out.append(f"{key} = {conf_value(value)}")

    if len(out) == 4:
        out.append("# (no settings changed yet)")

    for mon, cfg in state.get("monitors", {}).items():
        parts = [mon, cfg.get("mode", "preferred"), cfg.get("position", "auto"),
                 str(cfg.get("scale", 1))]
        if cfg.get("transform") in (1, 2, 3):
            parts.append(str(cfg["transform"]))
        if cfg.get("vrr"):
            parts += ["vrr", "1"]
        if cfg.get("disabled"):
            parts.append("disable")
        out.append("monitor = " + ", ".join(parts))

    for key in state.get("unbinds", []):
        out.append(f"unbind = {key}")

    for b in state.get("binds", []):
        out.append(conf_bind_line(b))

    out.append(f"windowrulev2 = float enabled, 1, class, {APP_CLASS}")
    out.append("windowrulev2 = size 1000 820, 1, class, " + APP_CLASS)
    out.append(f"windowrulev2 = center, 1, class, {APP_CLASS}")
    out.append(end)
    return "\n".join(out) + "\n"


def conf_syntax_error(text):
    """Structural sanity check for generated hyprland.conf text.

    Hyprland 0.56+ cannot parse .conf at all, so on a Lua install there is no
    real parser to validate against. This catches the mistakes we could
    actually make: unbalanced blocks, or a line that is neither a comment, a
    block header, nor a key = value.
    """
    depth = 0
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "}":
            depth -= 1
            if depth < 0:
                return f"line {n}: unmatched '}}'"
            continue
        if line.endswith("{"):
            name = line[:-1].strip()
            if not name or "=" in name:
                return f"line {n}: bad block header {line!r}"
            depth += 1
            continue
        if depth == 0:
            if "=" not in line:
                return f"line {n}: expected 'key = value' at top level, got {line!r}"
        elif "=" not in line:
            return f"line {n}: expected 'key = value' inside a block, got {line!r}"
    if depth != 0:
        return f"{depth} unclosed block(s) at end of file"
    return None


def _preamble():
    """Lines that are always present, whatever the user changed."""
    return [
        MARK_START,
        "--   o.bind/o.window are Omarchy helpers; define the two we use so this",
        "--   file also loads on a plain Hyprland install.",
        "if o == nil then o = {} end",
        "if o.bind == nil then",
        "  o.bind = function(keys, description, dispatcher, opts)",
        "    opts = opts or {}",
        "    if description then opts.description = description end",
        "    if type(dispatcher) == \"string\" then dispatcher = hl.dsp.exec_cmd(dispatcher) end",
        "    hl.bind(keys, dispatcher, opts)",
        "  end",
        "end",
        "--   Written by hypr-gui. Only the settings you changed in the GUI live",
        "--   here; everything else stays under Omarchy's and your own control.",
        "--   Safe to hand-edit, but the GUI rewrites this whole block.",
    ]


def disp_lookup(display):
    """(call path, arg key, plain, description) for a display name, or None."""
    for name, path, key, plain, desc in DISPATCHERS:
        if name == display:
            return path, key, plain, desc
    return None


def bind_dispatcher(b):
    """(lua path, arg key, plain, value) for a saved binding.

    An unknown or absent dispatcher falls back to exec, which is what the GUI
    wrote before the dispatcher dropdown existed.
    """
    found = disp_lookup(b.get("dispatcher") or "")
    if found is None:
        return "exec_cmd", None, True, b.get("command") or ""
    path, key, plain, _desc = found
    if path in ("exec_cmd", "exec_raw"):
        return path, key, True, b.get("command") or ""
    return path, key, plain, b.get("arg") or ""


def lua_bind_line(b):
    desc = lua_value(b.get("description") or "")
    path, key, plain, value = bind_dispatcher(b)
    if not value and not plain:
        return f'o.bind({lua_value(b["keys"])}, {desc}, hl.dsp.{path}())'
    if plain:
        if key in NUMERIC_ARGS and value.lstrip("-").isdigit():
            lit = value
        else:
            lit = lua_value(value)
        return f'o.bind({lua_value(b["keys"])}, {desc}, hl.dsp.{path}({lit}))' 
    if key in NUMERIC_ARGS and value.lstrip("-").isdigit():
        lit = value
    else:
        lit = lua_value(value)
    extra = "".join(f", {k} = {v}" for k, v in COMPANION_ARGS.get(path, {}).items()
                     if k != key)
    return (f'o.bind({lua_value(b["keys"])}, {desc}, '
            f'hl.dsp.{path}({{ {key} = {lit}{extra} }}))')


def conf_bind_line(b):
    path, _key, _plain, value = bind_dispatcher(b)
    disp = CONF_DISPATCHERS.get(path, path)
    return f'bind = {b["keys"]}, {disp}, {value}, {b.get("description") or ""}'


def build_lua(state):
    chunks = _preamble()

    tree = build_config_tree(state)
    if tree:
        chunks.append("hl.config(" + emit_table(tree, 0) + ")")

    for mon, cfg in state.get("monitors", {}).items():
        parts = [f'output = {lua_value(mon)}']
        for k, v in cfg.items():
            if v is None:
                continue
            parts.append(f"{k} = {lua_value(v)}")
        chunks.append("hl.monitor({ " + ", ".join(parts) + " })")

    for key in state.get("unbinds", []):
        chunks.append(f"hl.unbind({lua_value(key)})")

    for b in state.get("binds", []):
        chunks.append(lua_bind_line(b))

    if len(chunks) == len(_preamble()):
        chunks.append("-- (no settings changed yet)")

    # Keep this app's own window at a usable size on any workspace.
    # The Lua window_rule field is "float" (not "floating"), and size/center
    # only take effect at map time, so this has to be in the config on launch.
    chunks.append('hl.window_rule({ match = { class = "io.github.ppSpacker420.hyprgui" }, '
                  'float = true, size = { 1000, 820 }, center = true })')

    return "\n".join(chunks) + "\n" + MARK_END + "\n"


def lua_syntax_error(text):
    """Return luac's complaint about `text`, or None if it parses.

    A malformed generated file makes Hyprland refuse to load the whole config,
    so we check before touching the live one.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as fh:
        fh.write(text)
        tmp = fh.name
    try:
        proc = subprocess.run(["luac", "-p", tmp], capture_output=True, text=True)
        return proc.stderr.strip() or None if proc.returncode else None
    except FileNotFoundError:
        return None
    finally:
        os.unlink(tmp)


def install_generated_file(state=None):
    state = state if state is not None else load_state()
    style = config_style()
    if style == "conf":
        text = emit_conf(state)
        err = conf_syntax_error(text)
    else:
        text = build_lua(state)
        err = lua_syntax_error(text)
    if err:
        raise LuaSyntaxError(err)
    gen_file(style).write_text(text)


def ensure_required():
    """Make sure the main config loads our generated file.

    Lua gets a require(), .conf gets a source= line. Both are idempotent.
    """
    style = config_style()
    main = main_file(style)
    if not main.exists():
        return
    text = main.read_text()
    if style == "lua":
        if 'require("hypr.hyprgui")' in text:
            return
        lines = text.splitlines(keepends=True)
        last = max((i for i, l in enumerate(lines) if l.strip().startswith("require(")),
                   default=-1)
        lines.insert(last + 1, 'require("hypr.hyprgui")\n')
    else:
        if "hyprgui.conf" in text:
            return
        # Source it last so the GUI's settings win over the defaults above.
        lines = text.splitlines(keepends=True)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append("\n# Added by hypr-gui\nsource = ~/.config/hypr/hyprgui.conf\n")
    main.write_text("".join(lines))


def resync_monitors(state):
    """Hyprland quantizes scale per output, so store what actually took effect."""
    live = {m["name"]: m for m in (hypr_json("monitors") or [])}
    for name, cfg in state.get("monitors", {}).items():
        actual = live.get(name, {}).get("scale")
        if actual is not None and "scale" in cfg:
            cfg["scale"] = actual
    save_state(state)


def apply_now():
    target = gen_file()
    previous = target.read_text() if target.exists() else None
    install_generated_file()
    out = hypr("reload")
    errs = hypr("configerrors").strip()
    if errs:
        # Hyprland kept the old config but is now unhappy; put the last known
        # good file back rather than leaving the user with a broken WM.
        if previous is not None:
            target.write_text(previous)
            hypr("reload")
        return errs
    resync_monitors(load_state())
    return out


# --------------------------------------------------------------------------
# helpers for row construction
# --------------------------------------------------------------------------

def plain(text):
    """Group titles go through markup; a bare & would break parsing."""
    return text.replace("&", "&amp;")


def row_title(row, spec):
    row.set_title(spec["label"])
    sub = spec.get("subtitle")
    if sub:
        row.set_subtitle(sub)


def unsupported_row(spec):
    """Shown instead of a control this Hyprland build has no option for."""
    row = Adw.ActionRow(title=spec["label"])
    label = Gtk.Label(label="not in this Hyprland version", valign=Gtk.Align.CENTER)
    label.add_css_class("dim-label")
    label.add_css_class("caption")
    row.add_suffix(label)
    row.set_subtitle_lines(0)
    return row


class SettingRow:
    """One editable option: switch, spin button, dropdown or entry."""

    def __init__(self, window, spec, group_box):
        if spec["key"] in unsupported_options():
            group_box.add(unsupported_row(spec))
            return
        self.window = window
        self.spec = spec
        self.key = spec["key"]
        self.state = window.state
        self.saved = self.state["options"].get(self.key, get_option(self.key))
        self.value = self.saved if self.saved is not None else spec.get("default")

        t = spec["type"]
        if t == "bool":
            self.row = Adw.ActionRow()
            self.sw = Gtk.Switch(valign=Gtk.Align.CENTER)
            self.sw.set_active(bool(self.value))
            self.sw.connect("notify::active", self._changed)
            self.row.add_suffix(self.sw)
            self.row.set_activatable_widget(self.sw)
        elif t == "enum":
            self.row = Adw.ComboRow()
            model = Gtk.StringList()
            for o in spec["options"]:
                model.append(o)
            self.row.set_model(model)
            if self.value in spec["options"]:
                self.row.set_selected(spec["options"].index(self.value))
            self.row.connect("notify::selected", self._changed)
            self.row.set_subtitle(spec["options"][self.row.get_selected()]
                                  if self.row.get_selected() >= 0 else "unset")
        elif t == "str":
            self.row = Adw.EntryRow()
            self.row.set_text(str(self.value or ""))
            if spec.get("placeholder"):
                self.row.set_show_apply_button(False)
            self.entry = self.row
            self.row.connect("changed", self._changed)
        else:
            self.row = Adw.ActionRow()
            self.spin = Gtk.SpinButton(valign=Gtk.Align.CENTER)
            adj = Gtk.Adjustment(
                lower=float(spec.get("lo", 0)), upper=float(spec.get("hi", 100)),
                step_increment=float(spec.get("step", 1)), page_increment=10)
            self.spin.set_adjustment(adj)
            self.spin.set_digits(2 if t == "float" else 0)
            if self.value is not None:
                self.spin.set_value(float(self.value))
            self.spin.connect("value-changed", self._changed)
            self.row.add_suffix(self.spin)
            self.row.set_activatable_widget(self.spin)
            self.spin_val = float(self.value if self.value is not None else spec.get("lo", 0))

        row_title(self.row, spec)
        group_box.add(self.row)

    def _current(self):
        s, t = self.spec, self.spec["type"]
        if t == "bool":
            return self.sw.get_active()
        if t == "enum":
            i = self.row.get_selected()
            return s["options"][i] if i >= 0 else None
        if t == "str":
            return self.row.get_text().strip()
        if t == "int":
            return int(round(self.spin.get_value()))
        return round(self.spin.get_value(), 3)

    def _changed(self, *_):
        v = self._current()
        if v is None:
            return
        self.value = v
        if s := self.spec.get("subtitle"):
            self.row.set_subtitle(s)
        if self.spec["type"] == "enum" and self.spec.get("subtitle") is None:
            self.row.set_subtitle(str(v))
        if v == self.saved:
            self.state["options"].pop(self.key, None)
        else:
            self.state["options"][self.key] = v
        self.window.mark_dirty()


# --------------------------------------------------------------------------
# key capture
# --------------------------------------------------------------------------

MOD_ORDER = ["SUPER", "CTRL", "SHIFT", "ALT"]


class CommandEntry(Gtk.Box):
    """Search box plus a dropdown of every command found on this machine.

    Gtk.EntryCompletion is deprecated in GTK4, so this is a Gtk.DropDown fed by
    a Gtk.StringList. Gtk.FilterListModel cannot be constructed from Python, so
    typing rebuilds the (much smaller) list rather than filtering a big one in
    place. Rebuilding 3k strings is imperceptible and keeps this simple.
    """

    NONE = "(none)"

    def __init__(self, commands, placeholder="Search commands on your PATH"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.commands = list(commands)
        self.visible = [self.NONE] + self.commands

        self.search = Gtk.SearchEntry(placeholder_text=placeholder)
        self.search.connect("search-changed", self._on_search)
        self.append(self.search)

        self.dropdown = Gtk.DropDown(model=self._model())
        self.dropdown.set_hexpand(True)
        self.append(self.dropdown)

    @staticmethod
    def _matches(text, needle):
        """Subsequence match, so "ocs" finds omarchy-capture-screenshot."""
        if not needle:
            return True
        pos = 0
        for ch in text.lower():
            if pos < len(needle) and ch == needle[pos]:
                pos += 1
        return pos == len(needle)

    def _model(self):
        store = Gtk.StringList()
        for c in self.visible:
            store.append(c)
        return store

    def _on_search(self, entry):
        needle = entry.get_text().strip().lower()
        self.visible = [self.NONE] + [c for c in self.commands
                                      if self._matches(c, needle)]
        # The list is rebuilt, so the old selection index no longer means the
        # same thing; reset it.
        self.dropdown.set_model(self._model())
        self.dropdown.set_selected(0)

    def set_commands(self, commands):
        self.commands = list(commands)
        self.search.set_text("")
        self.visible = [self.NONE] + self.commands
        self.dropdown.set_model(self._model())
        self.dropdown.set_selected(0)

    def value(self):
        item = self.dropdown.get_selected_item()
        if item is None:
            return ""
        text = item.get_string()
        return "" if text == self.NONE else text

    def set_value(self, text):
        self.search.set_text("")
        if not text:
            self.set_commands(self.commands)
            return
        # Narrow to just the match so set_selected lands on the right row.
        if text in self.commands:
            self.visible = [self.NONE, text]
            self.dropdown.set_model(self._model())
            self.dropdown.set_selected(1)


class KeyEntry(Gtk.Box):
    """Entry that records a key combination when clicked."""

    def __init__(self, placeholder="Click, then press keys"):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.recording = False
        self.mods = set()
        self.keyval = 0
        self.keyval_name = ""

        self.entry = Gtk.Entry(hexpand=True, placeholder_text=placeholder)
        self.entry.set_text(placeholder)
        self.entry.set_editable(False)
        self.rec_btn = Gtk.ToggleButton(icon_name="input-keyboard-symbolic",
                                        tooltip_text="Record a key combination")
        self.rec_btn.connect("toggled", self._toggle)
        self.append(self.entry)
        self.append(self.rec_btn)

        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", self._key_pressed)
        self.entry.add_controller(ctrl)

    def value(self):
        if self.keyval_name and (self.mods or not self.keyval_name.isalnum()):
            parts = [m for m in MOD_ORDER if m in self.mods]
            parts.append(self.keyval_name)
            return " + ".join(parts)
        return self.entry.get_text().strip()

    def set_value(self, text):
        self.keyval_name = ""
        self.mods = set()
        self.entry.set_text(text)

    def _toggle(self, btn):
        self.recording = btn.get_active()
        if self.recording:
            self.mods = set()
            self.keyval = 0
            self.entry.set_text("Press keys…")
            self.entry.grab_focus()

    def _key_pressed(self, _c, keyval, keycode, state):
        if not self.recording:
            return False
        from gi.repository import Gdk

        if keyval in (Gdk.KEY_Escape,):
            self.rec_btn.set_active(False)
            self.set_value("")
            return True
        if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete):
            self.keyval_name = ""
            self.mods = set()
            self.entry.set_text("—")
            return True
        mask = Gdk.ModifierType(0)
        for mod, bit in (("SUPER", Gdk.ModifierType.SUPER_MASK),
                         ("CTRL", Gdk.ModifierType.CONTROL_MASK),
                         ("SHIFT", Gdk.ModifierType.SHIFT_MASK),
                         ("ALT", Gdk.ModifierType.ALT_MASK)):
            if state & bit:
                self.mods.add(mod)
        name = Gdk.keyval_name(keyval)
        if not name:
            return True
        self.keyval = keyval
        self.keyval_name = name
        self.rec_btn.set_active(False)
        self.entry.set_text(self.value())
        return True


# --------------------------------------------------------------------------
# main window
# --------------------------------------------------------------------------

class HyprGuiWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Hyprland Settings")
        # Tiling WMs ignore default_size and honour the minimum, so ask for a
        # sensible floor and let the WM fill the rest of the workspace.
        self.set_size_request(760, 540)
        self.state = load_state()
        self.groups = []

        header = Adw.HeaderBar()
        self.reload_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                     tooltip_text="Reload Hyprland config")
        self.reload_btn.connect("clicked", lambda *_: self.reload_clicked())
        header.pack_end(self.reload_btn)
        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu = Gio.Menu()
        menu.append("Reset all GUI changes", "win.reset")
        menu.append("Open generated file", "win.open_generated")
        menu.append("View config errors", "win.show_errors")
        self.menu_btn.set_menu_model(menu)
        header.pack_end(self.menu_btn)

        self.tabs = Adw.TabView()
        self.tabs.set_vexpand(True)
        # The tab bar belongs in the header's centre; as a plain box child it
        # gets clipped by the window controls.
        header.set_title_widget(Adw.TabBar(view=self.tabs, autohide=False))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(self.tabs)

        self.toast = Adw.ToastOverlay(child=box)
        self.set_content(self.toast)

        for prefs, title in self._build_pages():
            page = self.tabs.append(prefs)
            page.set_title(title)

        self.status = Adw.StatusPage(icon_name="emblem-ok-symbolic",
                                     title="No pending changes",
                                     description="Everything on disk matches the compositor.")
        self.status.set_vexpand(True)
        self.tabs.append(self.status).set_title("Status")

        actions = [
            ("reset", self.on_reset),
            ("open-generated", self.on_open_generated),
            ("show-errors", self.on_show_errors),
        ]
        for name, cb in actions:
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

    # -- pages ------------------------------------------------------------

    @staticmethod
    def new_page(title):
        """Adw.TabView.append() takes the page's child and titles it itself."""
        prefs = Adw.PreferencesPage()
        return prefs, title

    def _build_pages(self):
        pages = []
        for tab, spec_key in (("Look", "look"), ("Layout", "layout"),
                              ("Input", "mouse"), ("Keyboard", "keyboard")):
            prefs, title = self.new_page(tab)
            by_section = {}
            for spec in SCHEMA[spec_key]:
                by_section.setdefault(spec["section"], []).append(spec)
            for section, specs in by_section.items():
                group = Adw.PreferencesGroup()
                group.set_title(plain(section))
                for spec in specs:
                    SettingRow(self, spec, group)
                prefs.add(group)
            pages.append((prefs, title))
        pages.append(self._build_monitors_page())
        pages.append(self._build_binds_page())
        pages.append(self._build_files_page())
        return pages

    def _build_monitors_page(self):
        prefs, title = self.new_page("Displays")
        data = hypr_json("monitors", "all") or []
        seen = set()
        for m in data:
            name = m.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            prefs.add(self._monitor_group(m))
        return prefs, title

    def _monitor_group(self, m):
        name = m["name"]
        mon_cfg = self.state["monitors"].setdefault(name, {})
        group = Adw.PreferencesGroup(
            title=name,
            description=(f'{m.get("make","")} {m.get("model","")}').strip() or None)

        def setcfg(**kw):
            mon_cfg.update(kw)
            self.mark_dirty()

        # enabled switch
        row = Adw.ActionRow(title="Enabled", subtitle="Turn this output off or on")
        sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        if "disabled" in mon_cfg:
            sw.set_active(not mon_cfg["disabled"])
        else:
            sw.set_active(not m.get("disabled", False))
        sw.connect("notify::active", lambda s, *_: setcfg(disabled=not s.get_active()))
        row.add_suffix(sw)
        row.set_activatable_widget(sw)
        group.add(row)

        modes = m.get("availableModes") or []
        current = f'{m.get("width")}x{m.get("height")}'
        if m.get("transform", 0) in (1, 3):
            current = current + " (rotated)"
        row = Adw.ComboRow(title="Resolution")
        model = Gtk.StringList()
        model.append("preferred")
        for mode in modes:
            model.append(mode)
        row.set_model(model)
        want = mon_cfg.get("mode")
        if want is None or want == "preferred":
            row.set_selected(0)
        elif want in modes:
            row.set_selected(modes.index(want) + 1)
        row.set_subtitle(want or current)
        row.connect("notify::selected", lambda r, *_: setcfg(mode="preferred" if r.get_selected() == 0
                                                             else modes[r.get_selected() - 1]))
        group.add(row)

        row = Adw.ComboRow(title="Rotation")
        model = Gtk.StringList()
        for lbl in ("Normal", "90°", "180°", "270°"):
            model.append(lbl)
        row.set_model(model)
        t = mon_cfg.get("transform", m.get("transform", 0))
        row.set_selected(t if t in (0, 1, 2, 3) else 0)
        row.connect("notify::selected", lambda r, *_: setcfg(transform=r.get_selected()))
        group.add(row)

        row = Adw.ComboRow(title="Scale")
        model = Gtk.StringList()
        for s in ("1", "1.25", "1.5", "1.75", "2"):
            model.append(f"{s}×")
        row.set_model(model)
        scales = [1, 1.25, 1.5, 1.75, 2]
        s = mon_cfg.get("scale", m.get("scale", 1))
        row.set_selected(scales.index(s) if s in scales else 0)
        row.set_subtitle(f'{mon_cfg.get("scale", m.get("scale", 1))}×')
        row.connect("notify::selected", lambda r, *_: (setcfg(scale=scales[r.get_selected()]),
                                                       row.set_subtitle(f"{scales[r.get_selected()]}×")))
        group.add(row)

        row = Adw.ActionRow(title="Position", subtitle="auto, or XxY such as 1920x0")
        pos = Gtk.Entry(text=str(mon_cfg.get("position", m.get("x", 0) and f'{m.get("x")}x{m.get("y")}' or "auto")),
                        valign=Gtk.Align.CENTER, width_chars=12)
        pos.connect("changed", lambda e: setcfg(position=e.get_text().strip() or "auto"))
        row.add_suffix(pos)
        row.set_activatable_widget(pos)
        group.add(row)

        row = Adw.ActionRow(title="Variable refresh (VRR)")
        sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        sw.set_active(mon_cfg.get("vrr", m.get("vrr", False)))
        sw.connect("notify::active", lambda s, *_: setcfg(vrr=s.get_active()))
        row.add_suffix(sw)
        row.set_activatable_widget(sw)
        group.add(row)
        return group

    @staticmethod
    def _caption_label():
        """A dim, wrapping caption line that a PreferencesGroup can hold."""
        label = Gtk.Label(xalign=0, wrap=True)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        label.set_margin_start(12)
        label.set_margin_end(12)
        label.set_margin_bottom(4)
        return label

    def _build_binds_page(self):
        prefs, title = self.new_page("Keybindings")

        group = Adw.PreferencesGroup(
            title="Your keybindings",
            description="Added here, written to hyprgui.lua. They load last, so they win over defaults.")
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        self.binds_list = listbox
        group.add(listbox)
        self._rebuild_binds()
        prefs.add(group)

        add = Adw.PreferencesGroup(title="Add a binding")
        add.set_description(
            "Pick what the shortcut does, then record the keys. Your binding is "
            "written last, so it overrides any inherited binding for the same keys.")

        row = Adw.ActionRow(title="Shortcut")
        self.new_key = KeyEntry()
        self.new_key.set_size_request(320, -1)
        row.add_suffix(self.new_key)
        row.set_activatable_widget(self.new_key.entry)
        add.add(row)

        # --- dispatcher dropdown
        self.disp_combo = Adw.ComboRow(title="Action")
        model = Gtk.StringList()
        model.append("Run a command…")
        for name, _path, _key, _plain, _desc in DISPATCHERS:
            model.append(name)
        self.disp_combo.set_model(model)
        self.disp_combo.connect("notify::selected", lambda *_: self._disp_changed())
        add.add(self.disp_combo)
        self._disp_desc = self._caption_label()
        add.add(self._disp_desc)

        # --- argument box, shown only for dispatchers that take one
        self.arg_combo = Adw.ComboRow(title="Argument")
        self.arg_model = Gtk.StringList()
        self.arg_combo.set_model(self.arg_model)
        self.arg_combo.connect("notify::selected", lambda *_: self._sync_cmd())
        add.add(self.arg_combo)

        self.arg_entry = Adw.EntryRow(title="Argument")
        self.arg_entry.connect("changed", lambda *_: self._sync_cmd())
        add.add(self.arg_entry)

        # --- command autocomplete
        row = Adw.ActionRow(title="Command")
        self.cmd_box = CommandEntry(scan_commands())
        self.cmd_box.set_size_request(320, -1)
        row.add_suffix(self.cmd_box)
        add.add(row)

        row = Adw.ActionRow(title="Description")
        self.new_desc = Gtk.Entry(valign=Gtk.Align.CENTER, width_chars=24)
        self.new_desc.set_placeholder_text("Close window")
        row.add_suffix(self.new_desc)
        row.set_activatable_widget(self.new_desc)
        add.add(row)

        self.cmd_hint = self._caption_label()
        add.add(self.cmd_hint)

        btn = Gtk.Button(label="Add binding", halign=Gtk.Align.START)
        btn.add_css_class("suggested-action")
        btn.connect("clicked", lambda *_: self.add_bind())
        add.add(btn)
        self._disp_changed()
        prefs.add(add)

        ub = Adw.PreferencesGroup(
            title="Unbind defaults",
            description="Remove an inherited Omarchy binding by adding its key here.")
        self.unbind_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.unbind_list.add_css_class("boxed-list")
        self.unbind_combo = Adw.ComboRow()
        self._active_keys = self._read_active_keys()
        model = Gtk.StringList()
        for k in self._active_keys:
            model.append(k)
        self.unbind_combo.set_model(model)
        self.unbind_combo.set_title("Existing binding")
        self.unbind_combo.set_subtitle(f"{len(self._active_keys)} active")
        ub.add(self.unbind_combo)
        btn = Gtk.Button(label="Unbind it", halign=Gtk.Align.START, sensitive=bool(self._active_keys))
        btn.add_css_class("destructive-action")
        btn.connect("clicked", lambda *_: self.add_unbind())
        ub.add(btn)
        prefs.add(ub)
        return prefs, title

    def _read_active_keys(self):
        binds = hypr_json("binds") or []
        keys = []
        for b in binds:
            k = b.get("key", "")
            if not k:
                continue
            label = " + ".join([p for p in k.split(",") if p]) or k
            if b.get("description"):
                label = f"{label}  ({b['description']})"
            if label not in keys:
                keys.append(label)
        return keys

    def _rebuild_binds(self):
        box = self.binds_list
        for child in list(box):
            box.remove(child)
        binds = self.state["binds"]
        if not binds:
            row = Adw.ActionRow(title="No bindings added yet")
            row.set_subtitle("Add one below, or edit hypr/bindings.lua directly.")
            box.append(row)
            return
        for i, b in enumerate(binds):
            path, _key, arg, cmd = bind_dispatcher(b)
            what = (cmd or arg) if path in ("exec_cmd", "exec_raw") else (
                path + (f" {arg}" if arg else ""))
            row = Adw.ActionRow(title=b["keys"], subtitle=what)
            if b.get("description"):
                row.set_subtitle(f'{b["description"]}  ·  {what}')
            del_btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            del_btn.add_css_class("flat")
            del_btn.connect("clicked", lambda _b, idx=i: self.remove_bind(idx))
            row.add_suffix(del_btn)
            box.append(row)

    def _selected_dispatcher(self):
        """("exec", None, None) for the command row, else (path, key, desc)."""
        i = self.disp_combo.get_selected()
        if i <= 0:
            return "exec_cmd", "cmd", None
        _name, path, key, _plain, desc = DISPATCHERS[i - 1]
        return path, key, desc

    def _disp_changed(self):
        path, key, desc = self._selected_dispatcher()
        is_exec = path in ("exec_cmd", "exec_raw")
        self.cmd_box.set_visible(is_exec)
        self._disp_desc.set_text("Type any command; it runs through a shell."
                                 if is_exec else desc)

        display = self._current_display()
        suggestions = ARG_SUGGESTIONS.get(display, [])
        takes_arg = key is not None
        self.arg_combo.set_visible(takes_arg and bool(suggestions))
        self.arg_entry.set_visible(takes_arg and not suggestions)
        if takes_arg and suggestions:
            model = Gtk.StringList()
            for s in suggestions:
                model.append(s)
            self.arg_combo.set_model(model)
            self.arg_model = model
        self._sync_cmd()

    def _current_display(self):
        i = self.disp_combo.get_selected()
        return DISPATCHERS[i - 1][0] if i > 0 else "exec"

    def _current_arg(self):
        path, key, _desc = self._selected_dispatcher()
        if key is None:
            return ""
        display = self._current_display()
        if ARG_SUGGESTIONS.get(display):
            i = self.arg_combo.get_selected()
            return ARG_SUGGESTIONS[display][i] if 0 <= i < len(ARG_SUGGESTIONS[display]) else ""
        return self.arg_entry.get_text().strip()

    def _sync_cmd(self):
        path, key, _desc = self._selected_dispatcher()
        if path in ("exec_cmd", "exec_raw"):
            n = len(self.cmd_box.commands)
            self.cmd_hint.set_text(f"{n} commands found on your PATH")
        else:
            self.cmd_hint.set_text("")

    def add_bind(self):
        keys = self.new_key.value()
        if not keys:
            self.notify("Record a shortcut first")
            return
        path, key, _desc = self._selected_dispatcher()
        is_exec = path in ("exec_cmd", "exec_raw")
        arg = self._current_arg()
        command = self.cmd_box.value() if is_exec else ""
        if is_exec and not command:
            self.notify("Enter a command to run")
            return
        if not is_exec and key is not None and not arg:
            self.notify(f"{self._current_display()} needs an argument")
            return

        self.state["binds"].append({
            "keys": keys,
            "description": self.new_desc.get_text().strip(),
            "command": command,
            "dispatcher": self._current_display(),
            "arg": arg,
        })
        self.new_key.set_value("")
        self.new_desc.set_text("")
        self.cmd_box.set_value("")
        self._rebuild_binds()
        self.mark_dirty()

    def remove_bind(self, idx):
        del self.state["binds"][idx]
        self._rebuild_binds()
        self.mark_dirty()

    def add_unbind(self):
        i = self.unbind_combo.get_selected()
        if i < 0:
            return
        label = self._active_keys[i]
        key = label.split("  (")[0]
        if key not in self.state["unbinds"]:
            self.state["unbinds"].append(key)
        self.mark_dirty()
        self.notify(f"Unbind queued for {key} — hit Save")

    def _config_files(self):
        """Editable configs: Lua on 0.56+, .conf before that."""
        files = sorted(
            p.name for p in HYPR_DIR.iterdir()
            if p.is_file() and (p.suffix in (".lua", ".conf"))
            and not p.name.startswith(".")
        )
        return files

    def _load_file(self):
        files = self._config_files()
        i = self.file_combo.get_selected()
        if 0 <= i < len(files):
            return (HYPR_DIR / files[i]).read_text()
        return ""

    def _build_files_page(self):
        prefs, title = self.new_page("Config files")
        group = Adw.PreferencesGroup(
            title="Edit a config file",
            description="Saved straight to disk. Hyprland needs a reload to pick up binding changes.")
        self.file_combo = Adw.ComboRow()
        files = self._config_files()
        model = Gtk.StringList()
        for f in files:
            model.append(f)
        self.file_combo.set_model(model)
        self.file_combo.set_title("File")
        self.file_combo.connect("notify::selected", lambda *_: self.load_file())
        group.add(self.file_combo)

        self.view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.NONE,
                                 top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        self.view.set_vexpand(True)
        scroller = Gtk.ScrolledWindow(vexpand=True, child=self.view)
        group.add(scroller)

        btns = Gtk.Box(spacing=6, halign=Gtk.Align.START)
        save = Gtk.Button(label="Save file")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self.save_file())
        btns.append(save)
        reload = Gtk.Button(label="Reload Hyprland")
        reload.connect("clicked", lambda *_: self.reload_clicked())
        btns.append(reload)
        group.add(btns)
        prefs.add(group)
        GLib.idle_add(self.load_file)
        return prefs, title

    def load_file(self):
        self.view.get_buffer().set_text(self._load_file())
        return False

    def save_file(self):
        files = self._config_files()
        i = self.file_combo.get_selected()
        if 0 <= i < len(files):
            p = HYPR_DIR / files[i]
            p.write_text(self.view.get_buffer().get_text(
                self.view.get_buffer().get_start_iter(),
                self.view.get_buffer().get_end_iter(), False))
            self.notify(f"Saved {p.name} — reload to apply")

    # -- actions ----------------------------------------------------------

    def mark_dirty(self):
        save_state(self.state)
        try:
            install_generated_file()
        except LuaSyntaxError as exc:
            self.notify(f"Not written - generated Lua is invalid: {exc}")
            return
        self.status.set_title("Unapplied changes")
        n = (len(self.state["options"]) + len(self.state["monitors"])
             + len(self.state["binds"]) + len(self.state["unbinds"]))
        self.status.set_description(
            f"{n} GUI-managed setting(s) written to {gen_file()}.\n"
            "Press the reload button (top right) to apply them now.")
        self.tabs.set_selected_page(self.tabs.get_nth_page(self.tabs.get_n_pages() - 1))

    def reload_clicked(self):
        ensure_required()
        try:
            out = apply_now()
        except LuaSyntaxError as exc:
            self.notify(f"Not applied - generated Lua is invalid: {exc}")
            return
        if out == "ok":
            self.notify("Hyprland config reloaded")
        else:
            self.notify(f"Hyprland rejected the config, rolled back: {out[:160]}")
            return
        self.status.set_title("No pending changes")
        self.status.set_description("Everything on disk matches the compositor.")

    def on_reset(self, *_):
        self.state = {"options": {}, "monitors": {}, "binds": [], "unbinds": []}
        save_state(self.state)
        try:
            install_generated_file()
            apply_now()
        except LuaSyntaxError as exc:
            self.notify(f"Reset blocked - generated Lua is invalid: {exc}")
            return
        self.notify("Cleared all GUI-managed settings")

    def on_open_generated(self, *_):
        run("xdg-open", str(gen_file()))

    def on_show_errors(self, *_):
        errs = hypr("configerrors").strip()
        dlg = Adw.MessageDialog(transient_for=self, modal=True,
                                heading="Config errors" if errs else "No config errors",
                                body=errs[:2000] if errs else "Hyprland parsed everything cleanly.")
        dlg.add_response("close", "Close")
        dlg.present()

    def notify(self, msg):
        self.toast.add_toast(Adw.Toast.new(msg, timeout=3))


def preflight():
    """Return a list of human-readable problems that would break this session."""
    problems = []
    if not hypr("version").strip():
        problems.append("hyprctl is not talking to a running Hyprland session "
                        "(is $HYPRLAND_INSTANCE_SIGNATURE set?)")
    if not HYPR_DIR.exists():
        problems.append(f"{HYPR_DIR} does not exist")
    if not main_file().exists():
        problems.append(f"{main_file()} not found")
    # A window rule from a previous install of this app under a different
    # application id would keep matching a window that no longer exists.
    stale = re.findall(r"windowrulev2?.*?class,\s*([\w.]+)", main_file().read_text())
    stale = [c for c in stale if c.startswith(("dev.local.", "io.github.")) and c != APP_CLASS]
    if stale:
        problems.append(f"a stale window rule for {', '.join(sorted(set(stale)))} is in "
                        f"{main_file().name} - remove it or the old app id still matches")
    try:
        subprocess.run(["luac", "-v"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        problems.append("luac not found - the config syntax check will be skipped")
    return problems


class HyprGuiApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.ppSpacker420.hyprgui",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.window = None

    def do_activate(self):
        if self.window is None:
            problems = preflight()
            if problems:
                self._show_problems(problems)
                return
            self.window = HyprGuiWindow(self)
        self.window.present()

    def _show_problems(self, problems):
        w = Adw.ApplicationWindow(application=self, title="Hyprland Settings")
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Cannot start",
            description="hypr-gui needs a working Hyprland session and its config directory.")
        for text in problems:
            row = Adw.ActionRow(title=text)
            row.set_subtitle_lines(0)
            group.add(row)
        page.add(group)
        w.set_content(page)
        w.set_default_size(560, 260)
        w.present()


if __name__ == "__main__":
    HyprGuiApp().run()
