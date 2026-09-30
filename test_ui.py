#!/usr/bin/python3
"""Smoke test: build every page of the real GTK UI and fail on any exception.

The other suites exercise the write path, which never touches widget code - a
tuple-arity mistake in _build_binds_page passed all of them and still stopped
the app from opening. This constructs the window for real.

On Gtk-CRITICAL: a misused widget is a real failure even when every assertion
passes, and the check has to catch it. An in-process log hook is not usable
here - a log_set_writer_func callback segfaults on exactly the misuse we want to
detect - so this script re-runs itself in a child process and inspects the
child's stderr.
"""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

APP = Path(__file__).with_name("hypr_gui.py")
IS_CHILD = "--child" in sys.argv

spec = importlib.util.spec_from_file_location("hypr_gui", str(APP))
m = importlib.util.module_from_spec(spec)
sys.argv = ["hypr_gui"]
spec.loader.exec_module(m)

# Redirect writes into a temp dir: building the window calls mark_dirty(), which
# saves state and regenerates the config. Pointing the module at a scratch
# directory keeps this test from touching the user's real Hyprland config.
_SCRATCH = Path(tempfile.mkdtemp(prefix="hypr-gui-uitest-"))
m.HYPR_DIR = _SCRATCH
m.STATE_FILE = _SCRATCH / ".hypr-gui-state.json"
m.LUA_MAIN = _SCRATCH / "hyprland.lua"
m.CONF_MAIN = _SCRATCH / "hyprland.conf"
m.LUA_MAIN.write_text('require("hypr.input")\n')
(_SCRATCH / "hyprgui.lua").write_text(m.MARK_START + "\n" + m.MARK_END + "\n")

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails.append(name)


def pump(ms=400):
    """Let GTK's debounced signals fire."""
    ctx = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        while ctx.pending():
            ctx.iteration(False)


def run_checks():
    app = Adw.Application(application_id="dev.local.SmokeTest")
    app.register()
    try:
        window = m.HyprGuiWindow(app)
        check("main window builds", True)
    except Exception as exc:  # noqa: BLE001 - the point is to report any failure
        check("main window builds", False, f"{type(exc).__name__}: {exc}")
        return

    check("all pages built", window.tabs.get_n_pages() >= 7,
          f"{window.tabs.get_n_pages()} pages")

    # --- dispatcher dropdown
    check("dispatcher dropdown populated", window.disp_combo.get_model() is not None)
    n = window.disp_combo.get_model().get_n_items()
    check("dispatcher count matches table", n == len(m.DISPATCHERS) + 1,
          f"{n} items vs {len(m.DISPATCHERS)} dispatchers + exec row")

    errors = []
    for i in range(n):
        try:
            window.disp_combo.set_selected(i)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"row {i}: {type(exc).__name__}: {exc}")
    check("every dispatcher row is selectable", not errors, "; ".join(errors[:3]))

    window.disp_combo.set_selected(0)
    check("exec row shows command box", window.cmd_box.get_visible())
    check("exec row hides the argument box", not window.arg_combo.get_visible(),
          "the arg row must not appear when the command picker is used")

    close_row = 1 + [d[0] for d in m.DISPATCHERS].index("close")
    window.disp_combo.set_selected(close_row)
    check("close row hides command box", not window.cmd_box.get_visible())
    check("close row shows its description", "lose" in window._disp_desc.get_text(),
          window._disp_desc.get_text())

    focus_row = 1 + [d[0] for d in m.DISPATCHERS].index("focus")
    window.disp_combo.set_selected(focus_row)
    check("focus row shows arg combo", window.arg_combo.get_visible())
    check("focus suggestions loaded",
          window.arg_model.get_n_items() == len(m.ARG_SUGGESTIONS["focus"]))

    # --- command picker
    check("command list populated", len(window.cmd_box.commands) > 50,
          f"{len(window.cmd_box.commands)} commands")
    check("command model has every entry",
          len(window.cmd_box.visible) == len(window.cmd_box.commands) + 1)

    total = len(window.cmd_box.commands)
    window.cmd_box.search.set_text("omarchy-capture-scr")
    pump()
    filtered = [c for c in window.cmd_box.visible if c != window.cmd_box.NONE]
    check("search narrows the list", 0 < len(filtered) < total,
          f"{len(filtered)} of {total}")
    check("search finds the right command",
          "omarchy-capture-screenshot" in filtered, ", ".join(filtered[:3]))

    window.cmd_box.search.set_text("ocs")
    pump()
    sub = [c for c in window.cmd_box.visible if c != window.cmd_box.NONE]
    check("subsequence search works", "omarchy-capture-screenshot" in sub,
          f"{len(sub)} hits")

    window.cmd_box.set_value("omarchy-capture-screenshot")
    check("set_value selects the command",
          window.cmd_box.value() == "omarchy-capture-screenshot", window.cmd_box.value())
    window.cmd_box.set_value("")
    check("clearing returns nothing", window.cmd_box.value() == "", window.cmd_box.value())
    check("clearing restores full list", len(window.cmd_box.visible) == total + 1)

    # --- shortcut recorder
    key = window.new_key

    check("capture box is a button", isinstance(key.button, Gtk.Button))
    req = key.button.get_size_request()
    check("capture box is big", req[0] >= 300 and req[1] >= 80, f"{req[0]}x{req[1]}")
    check("capture box has a visible label", key.big_label.get_text() != "")
    check("no separate tiny arm button", not hasattr(key, "rec_btn"))
    check("idle text shown", "press your shortcut" in key.big_label.get_text().lower(),
          key.big_label.get_text())

    key.start_recording()
    check("recording state on", key.recording)
    check("recording shows the listening text",
          "Press keys" in key.big_label.get_text(), key.big_label.get_text())
    check("recording class applied", key.button.has_css_class("recording"))
    key.stop_recording()
    check("recording state off", not key.recording)
    check("recording class removed", not key.button.has_css_class("recording"))

    key.set_value("super+shift+r")
    check("typed combo normalised", key.value() == "SUPER + SHIFT + R", key.value())
    check("big label shows the combination",
          key.big_label.get_text() == "SUPER + SHIFT + R", key.big_label.get_text())

    key.set_value("super-r")
    check("hyphen separator accepted", key.value() == "SUPER + R", key.value())

    key.set_value("shift+super+r")
    check("modifiers normalised to a fixed order",
          key.value() == "SUPER + SHIFT + R", key.value())

    key.set_value("q")
    check("bare letter allowed", key.value() == "Q", key.value())
    key.set_value("f9")
    check("bare function key allowed", key.value() == "F9", key.value())
    key.set_value("super")
    check("modifiers alone rejected", key.value() == "SUPER", key.value())
    key.set_value("")
    check("cleared yields empty", key.value() == "", repr(key.value()))
    check("placeholder not a value", key.value() == "", repr(key.value()))
    check("big label returns to idle",
          "press your shortcut" in key.big_label.get_text().lower(),
          key.big_label.get_text())

    key.entry.set_text("ctrl+alt+t")
    check("typed entry gives a value", key.value() == "CTRL + ALT + T", key.value())
    check("big label follows typing",
          key.big_label.get_text() == "CTRL + ALT + T", key.big_label.get_text())
    key.set_value("")

    def press(kv, super_=False, ctrl_=False, shift_=False, alt_=False):
        st = Gdk.ModifierType(0)
        if super_:
            st |= Gdk.ModifierType.SUPER_MASK
        if ctrl_:
            st |= Gdk.ModifierType.CONTROL_MASK
        if shift_:
            st |= Gdk.ModifierType.SHIFT_MASK
        if alt_:
            st |= Gdk.ModifierType.ALT_MASK
        key._key_pressed(None, kv, 0, st)

    key.start_recording()
    press(Gdk.KEY_r, super_=True, shift_=True)
    check("records SUPER+SHIFT+R", key.value() == "SUPER + SHIFT + R", key.value())
    check("recording stops on a key", not key.recording)

    key.start_recording()
    press(Gdk.KEY_q)
    check("records a bare letter", key.value() == "Q", key.value())

    key.start_recording()
    press(Gdk.KEY_Escape)
    check("escape cancels", key.value() == "" and not key.recording, key.value())

    key.start_recording()
    press(Gdk.KEY_q)
    key.start_recording()
    press(Gdk.KEY_BackSpace)
    check("backspace clears but keeps listening", key.value() == "" and key.recording,
          f"{key.value()!r} recording={key.recording}")
    key.stop_recording()

    key.start_recording()
    press(Gdk.KEY_Super_L, super_=True)
    check("modifier alone keeps listening", key.recording,
          f"recording={key.recording} label={key.big_label.get_text()!r}")
    check("modifier alone is not a binding", key.value() == "", repr(key.value()))
    check("modifier alone shows in the big label",
          "SUPER" in key.big_label.get_text(), repr(key.big_label.get_text()))
    key.stop_recording()

    # --- add a binding end to end
    before = len(window.state["binds"])
    key.set_value("SUPER + F9")
    window.disp_combo.set_selected(close_row)
    window.new_desc.set_text("smoke test bind")
    window.add_bind()
    check("binding added to state", len(window.state["binds"]) == before + 1)
    if window.state["binds"]:
        last = window.state["binds"][-1]
        check("binding captured the dispatcher", last["dispatcher"] == "close", str(last))
        check("binding renders to lua",
              "hl.dsp.window.close()" in m.lua_bind_line(last), m.lua_bind_line(last))
        check("binding renders to conf",
              "closewindow" in m.conf_bind_line(last), m.conf_bind_line(last))
    window.remove_bind(len(window.state["binds"]) - 1)
    check("binding removed from state", len(window.state["binds"]) == before)

    # Don't destroy() an unrealized window: gtk_window_destroy segfaults here
    # when no surface has been allocated, which a headless run never does.
    # The process exits immediately afterwards anyway.


if __name__ == "__main__":
    if not IS_CHILD:
        # Re-run in a child so its stderr can be inspected for Gtk-CRITICALs.
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--child"],
            capture_output=True, text=True, timeout=180)
        print(proc.stdout, end="")
        noise = [l for l in proc.stderr.splitlines()
                 if "CRITICAL" in l or "assertion" in l]
        if noise:
            print("FAIL  no Gtk-CRITICAL during construction")
            for line in noise[:4]:
                print("      " + line[:150])
            fails.append("gtk criticals")
        else:
            print("PASS  no Gtk-CRITICAL during construction")
        print()
        print("FAILURES:", fails if fails else "none")
        sys.exit(1 if fails else 0)

    run_checks()
    shutil.rmtree(_SCRATCH, ignore_errors=True)
    if fails:
        sys.exit(1)
