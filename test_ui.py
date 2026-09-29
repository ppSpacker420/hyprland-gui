#!/usr/bin/python3
"""Smoke test: build every page of the real GTK UI and fail on any exception.

The other suites exercise the write path, which never touches widget code - a
tuple-arity mistake in _build_binds_page passed all of them and still stopped
the app from opening. This constructs the window for real.
"""
import importlib.util
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "hypr_gui", str(Path(__file__).with_name("hypr_gui.py")))
m = importlib.util.module_from_spec(spec)
sys.argv = ["hypr_gui"]
spec.loader.exec_module(m)

# Redirect all writes into a temp dir: building the window calls mark_dirty(),
# which saves state and regenerates the config. Pointing the module at a scratch
# directory keeps this test from touching the user's real Hyprland config.
import tempfile  # noqa: E402

_SCRATCH = Path(tempfile.mkdtemp(prefix="hypr-gui-uitest-"))
m.HYPR_DIR = _SCRATCH
m.STATE_FILE = _SCRATCH / ".hypr-gui-state.json"
m.LUA_MAIN = _SCRATCH / "hyprland.lua"
m.CONF_MAIN = _SCRATCH / "hyprland.conf"
m.LUA_MAIN.write_text('require("hypr.input")\n')
(_SCRATCH / "hyprgui.lua").write_text(m.MARK_START + "\n" + m.MARK_END + "\n")
m.GEN_CACHE = None

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails.append(name)


app = Adw.Application(application_id="dev.local.SmokeTest")
app.register()
window = None
try:
    window = m.HyprGuiWindow(app)
    check("main window builds", True)
except Exception as exc:  # noqa: BLE001 - the point is to report any failure
    check("main window builds", False, f"{type(exc).__name__}: {exc}")
    print()
    print("FAILURES:", fails)
    sys.exit(1)

# every tab actually got built
check("all pages built", window.tabs.get_n_pages() >= 7,
      f"{window.tabs.get_n_pages()} pages")

# the dispatcher dropdown is populated and wired
check("dispatcher dropdown populated", window.disp_combo.get_model() is not None)
n = window.disp_combo.get_model().get_n_items()
check("dispatcher count matches table", n == len(m.DISPATCHERS) + 1,
      f"{n} items vs {len(m.DISPATCHERS)} dispatchers + exec row")

# selecting each dispatcher must not raise - this is what the dropdown does live
errors = []
for i in range(n):
    try:
        window.disp_combo.set_selected(i)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"row {i}: {type(exc).__name__}: {exc}")
check("every dispatcher row is selectable", not errors, "; ".join(errors[:3]))

# the command picker got a real list
check("command list populated", len(window.cmd_box.commands) > 50,
      f"{len(window.cmd_box.commands)} commands")
check("command model has every entry",
      len(window.cmd_box.visible) == len(window.cmd_box.commands) + 1)

# searching narrows the list; subsequence match so "ocs" finds
# omarchy-capture-screenshot.
#
# Gtk.SearchEntry debounces "search-changed" by ~150ms, so pump the main loop
# rather than asserting immediately after set_text().
def settle(ms=400):
    """Let the debounced search signal fire without blocking on a real loop."""
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        while ctx.pending():
            ctx.iteration(False)


total = len(window.cmd_box.commands)
window.cmd_box.search.set_text("omarchy-capture-scr")
settle()
filtered = [c for c in window.cmd_box.visible if c != window.cmd_box.NONE]
check("search narrows the list", 0 < len(filtered) < total,
      f"{len(filtered)} of {total}")
check("search finds the right command",
      "omarchy-capture-screenshot" in filtered, ", ".join(filtered[:3]))

# a short subsequence query still matches
window.cmd_box.search.set_text("ocs")
settle()
sub = [c for c in window.cmd_box.visible if c != window.cmd_box.NONE]
check("subsequence search works", "omarchy-capture-screenshot" in sub,
      f"{len(sub)} hits")

# picking a command returns it
window.cmd_box.set_value("omarchy-capture-screenshot")
check("set_value selects the command",
      window.cmd_box.value() == "omarchy-capture-screenshot", window.cmd_box.value())
window.cmd_box.set_value("")
check("clearing returns nothing", window.cmd_box.value() == "", window.cmd_box.value())
check("clearing restores full list",
      len(window.cmd_box.visible) == total + 1)

# switching back to "run a command" shows the command box and hides the arg box
window.disp_combo.set_selected(0)
check("exec row shows command box", window.cmd_box.get_visible())
window.disp_combo.set_selected(1 + [d[0] for d in m.DISPATCHERS].index("close"))
check("close row hides command box", not window.cmd_box.get_visible())
check("close row shows its description", "lose" in window._disp_desc.get_text(),
      window._disp_desc.get_text())

# a dispatcher with suggestions shows the arg combo
window.disp_combo.set_selected(1 + [d[0] for d in m.DISPATCHERS].index("focus"))
check("focus row shows arg combo", window.arg_combo.get_visible())
check("focus suggestions loaded",
      window.arg_model.get_n_items() == len(m.ARG_SUGGESTIONS["focus"]))

# adding a binding end to end
before = len(window.state["binds"])
window.new_key.set_value("SUPER + F9")
window.disp_combo.set_selected(1 + [d[0] for d in m.DISPATCHERS].index("close"))
window.new_desc.set_text("smoke test bind")
window.add_bind()
check("binding added to state", len(window.state["binds"]) == before + 1)
if window.state["binds"]:
    last = window.state["binds"][-1]
    check("binding captured the dispatcher", last["dispatcher"] == "close", str(last))
    check("binding renders to lua",
          "hl.dsp.window.close()" in m.lua_bind_line(last),
          m.lua_bind_line(last))
    check("binding renders to conf",
          "closewindow" in m.conf_bind_line(last), m.conf_bind_line(last))

# and it is removed again
window.remove_bind(len(window.state["binds"]) - 1)
check("binding removed from state", len(window.state["binds"]) == before)

print()
print("FAILURES:", fails if fails else "none")

# Leave nothing behind, and prove the real config was never touched.
import shutil  # noqa: E402
shutil.rmtree(_SCRATCH, ignore_errors=True)
print(f"scratch dir removed: {not _SCRATCH.exists()}")
sys.exit(1 if fails else 0)
