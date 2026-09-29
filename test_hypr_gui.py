#!/usr/bin/python3
"""hypr-gui tests.

Two parts:
  * a live part that writes to the running compositor and verifies the result
  * an offline part that exercises both config backends (Lua and hyprland.conf)
    in a temp directory, so the .conf path is covered even though a 0.56+
    Hyprland cannot parse .conf itself.

Run:  python3 test_hypr_gui.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP = Path(__file__).with_name("hypr_gui.py")

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails.append(name)


def load_module():
    spec = importlib.util.spec_from_file_location("hypr_gui", APP)
    m = importlib.util.module_from_spec(spec)
    sys.argv = ["hypr_gui"]
    spec.loader.exec_module(m)
    return m


# ==========================================================================
# offline: both backends, in a sandbox
# ==========================================================================

SAMPLE = {
    "options": {
        "general:gaps_in": 12,
        "decoration:rounding": 10,
        "input:touchpad:natural_scroll": True,
        "input:kb_layout": "us,dk",
        "animations:enabled": False,
    },
    "monitors": {"HDMI-A-1": {"mode": "preferred", "position": "auto",
                              "scale": 1.25, "transform": 1, "vrr": True}},
    "binds": [{"keys": "SUPER + SHIFT + F12", "description": "GUI test bind",
               "command": "true"}],
    "unbinds": ["SUPER + SHIFT + B"],
}


def test_backends():
    """Render both formats into a temp config dir and sanity check them."""
    m = load_module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        m.HYPR_DIR = root
        m.LUA_MAIN = root / "hyprland.lua"
        m.CONF_MAIN = root / "hyprland.conf"
        m.STATE_FILE = root / ".hypr-gui-state.json"
        m._UNSUPPORTED.clear()

        # ---- Lua backend
        m.LUA_MAIN.write_text('require("default.hypr.omarchy")\nrequire("hypr.input")\n')
        check("detects lua config", m.config_style() == "lua")
        m.save_state(SAMPLE)
        m.install_generated_file()
        lua_out = (root / "hyprgui.lua")
        check("lua file written", lua_out.exists())
        check("lua parses", m.lua_syntax_error(lua_out.read_text()) is None)
        text = lua_out.read_text()
        check("lua nests input.touchpad", "touchpad = {" in text)
        check("lua quotes strings", '"us,dk"' in text)
        check("lua has no o.window call", "o.window(" not in text)
        check("lua defines o.bind shim", "if o.bind == nil then" in text)
        check("lua round-trips", m.build_lua(m.load_state()) == text)
        m.ensure_required()
        check("lua injected into main config",
              'require("hypr.hyprgui")' in m.LUA_MAIN.read_text())
        m.ensure_required()
        check("lua injection is idempotent",
              m.LUA_MAIN.read_text().count('require("hypr.hyprgui")') == 1)

        # ---- .conf backend
        m.LUA_MAIN.unlink()
        m.CONF_MAIN.write_text("general {\n  gaps_out = 2\n}\n")
        check("detects conf config", m.config_style() == "conf")
        m.install_generated_file()
        conf_out = root / "hyprgui.conf"
        check("conf file written", conf_out.exists())
        ctext = conf_out.read_text()
        check("conf is structurally valid", m.conf_syntax_error(ctext) is None,
              str(m.conf_syntax_error(ctext)))
        check("conf uses nested blocks",
              "input {" in ctext and "touchpad {" in ctext)
        check("conf booleans are bare", "natural_scroll = true" in ctext)
        check("conf has no lua", "hl.config" not in ctext)
        check("conf monitor line", "monitor = HDMI-A-1, preferred, auto, 1.25, 1, vrr, 1" in ctext)
        check("conf bind line", "bind = SUPER + SHIFT + F12, exec, true, GUI test bind" in ctext)
        check("conf unbind line", "unbind = SUPER + SHIFT + B" in ctext)
        m.ensure_required()
        check("conf sourced from main config", "hyprgui.conf" in m.CONF_MAIN.read_text())
        m.ensure_required()
        check("conf injection is idempotent",
              m.CONF_MAIN.read_text().count("hyprgui.conf") == 1)
        check("conf source is last line",
              m.CONF_MAIN.read_text().strip().endswith("hyprgui.conf"))

        # ---- structural checker actually catches breakage
        check("conf checker spots unclosed block",
              m.conf_syntax_error("general {\n  gaps_in = 1\n") is not None)
        check("conf checker spots stray brace",
              m.conf_syntax_error("general {\n}\n}\n") is not None)
        check("conf checker spots bad line",
              m.conf_syntax_error("this is not a setting\n") is not None)
        check("conf checker passes valid conf", m.conf_syntax_error(ctext) is None)

        # ---- lua checker catches breakage
        check("lua checker spots garbage",
              m.lua_syntax_error("-- [[ X ]]\nthis is not lua\n") is not None)
        check("lua checker passes valid lua",
              m.lua_syntax_error(text) is None)

        # ---- empty state is explicit in both formats
        empty = {"options": {}, "monitors": {}, "binds": [], "unbinds": []}
        check("lua empty state noted", "no settings changed yet" in m.build_lua(empty))
        check("conf empty state noted", "no settings changed yet" in m.emit_conf(empty))
        check("conf empty state valid", m.conf_syntax_error(m.emit_conf(empty)) is None)

        # ---- a broken generate is never written, in either backend
        for style in ("lua", "conf"):
            # Clear both, then create only the one under test: config_style()
            # prefers Lua when both exist.
            m.LUA_MAIN.unlink(missing_ok=True)
            m.CONF_MAIN.unlink(missing_ok=True)
            (m.LUA_MAIN if style == "lua" else m.CONF_MAIN).write_text("x\n")
            check(f"{style} style detected", m.config_style() == style)
            good = m.gen_file().read_text()
            orig = m.build_lua if style == "lua" else m.emit_conf
            setattr(m, "build_lua" if style == "lua" else "emit_conf",
                    lambda s: "garbage that is not valid syntax !!")
            try:
                m.install_generated_file()
                check(f"{style} bad generate rejected", False, "it was written")
            except m.LuaSyntaxError:
                check(f"{style} bad generate rejected", True)
            finally:
                setattr(m, "build_lua" if style == "lua" else "emit_conf", orig)
            check(f"{style} live file untouched", m.gen_file().read_text() == good)


# ==========================================================================
# live: writes to the running compositor
# ==========================================================================

def jcmd(*args):
    return json.loads(subprocess.run(["hyprctl", "-j", *args],
                                     capture_output=True, text=True).stdout)


def test_live():
    m = load_module()
    if m.preflight():
        print("\nSKIP  live tests: preflight failed: " + "; ".join(m.preflight()))
        return

    base_gaps = m.get_option("general:gaps_in")
    check("preflight clean", True)
    check("get_option int", isinstance(base_gaps, int), str(base_gaps))
    check("get_option float", isinstance(m.get_option("input:sensitivity"), float))
    check("get_option bool", m.get_option("animations:enabled") is True)
    check("get_option missing", m.get_option("no:such:option") is None)
    check("unsupported probe runs", isinstance(m.unsupported_options(), set))

    m.save_state(SAMPLE)
    check("reload ok", m.apply_now() == "ok")
    time.sleep(1.0)

    errs = subprocess.run(["hyprctl", "configerrors"], capture_output=True,
                          text=True).stdout.strip()
    check("no config errors", not errs, errs[:300])
    check("gaps_in applied", m.get_option("general:gaps_in") == 12,
          str(m.get_option("general:gaps_in")))
    check("rounding applied", m.get_option("decoration:rounding") == 10)
    check("touchpad applied", m.get_option("input:touchpad:natural_scroll") is True)
    check("kb_layout applied", m.get_option("input:kb_layout") == "us,dk")

    hdmi = [x for x in jcmd("monitors") if x["name"] == "HDMI-A-1"][0]
    check("monitor scale in sync",
          m.load_state()["monitors"]["HDMI-A-1"]["scale"] == hdmi["scale"],
          f'written={m.load_state()["monitors"]["HDMI-A-1"]["scale"]} live={hdmi["scale"]}')

    binds = jcmd("binds")
    check("bind applied",
          any(b["key"] == "F12" and b.get("description") == "GUI test bind" for b in binds))
    check("unbind applied",
          not any(b["key"] == "B" and b.get("modmask") == 64 for b in binds))

    # rollback: a config the compositor rejects must be undone
    good = m.gen_file().read_text()
    orig = m.hypr
    m.hypr = lambda *a: ("hyprgui: syntax error" if a[0] == "configerrors"
                         else orig(*a))
    try:
        out = m.apply_now()
    finally:
        m.hypr = orig
    check("rejected config reported", "syntax error" in out, out[:60])
    check("rolled back to last good file", m.gen_file().read_text() == good)
    time.sleep(0.5)
    check("compositor clean after rollback", not m.hypr("configerrors").strip())

    # restore
    m.save_state({"options": {"general:gaps_in": base_gaps},
                  "monitors": {}, "binds": [], "unbinds": []})
    m.apply_now()
    time.sleep(0.8)
    check("restored gaps_in", m.get_option("general:gaps_in") == base_gaps)
    check("test bind gone", not any(b["key"] == "F12" for b in jcmd("binds")))


if __name__ == "__main__":
    print("== offline: config backends ==")
    test_backends()
    print("\n== live: running compositor ==")
    test_live()
    print()
    print("FAILURES:", fails if fails else "none")
    sys.exit(1 if fails else 0)
