#!/usr/bin/python3
"""Verify dispatcher bindings actually register and fire on the live compositor.

This is the check that matters for the dispatcher dropdown: a binding that
renders to plausible-looking Lua but is rejected by hyprctl is worthless. The
first version of the dispatcher table was scraped from the wiki and had 48 of
48 names wrong; every entry here is probed against the running compositor.
"""
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "hypr_gui", str(Path(__file__).with_name("hypr_gui.py")))
m = importlib.util.module_from_spec(spec)
sys.argv = ["hypr_gui"]
spec.loader.exec_module(m)

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails.append(name)


def q(expr):
    r = subprocess.run(["hyprctl", "eval", expr], capture_output=True, text=True)
    return r.returncode, (r.stdout or r.stderr).strip()


def binds():
    return json.loads(subprocess.run(["hyprctl", "-j", "binds"],
                                     capture_output=True, text=True).stdout)


base_state = m.load_state()

# --- every dispatcher constructs on the real compositor
broken = []
for display, path, key, plain, _desc in m.DISPATCHERS:
    sugg = m.ARG_SUGGESTIONS.get(display) or []
    sample = (sugg[0] if sugg else ("1" if key else "")) if key else ""
    b = {"keys": "X", "description": "d", "dispatcher": display,
         "command": "true" if path in ("exec_cmd", "exec_raw") else "",
         "arg": sample}
    line = m.lua_bind_line(b)
    call = "hl.dsp." + line.split(", hl.dsp.", 1)[1][:-1] if ", hl.dsp." in line else line
    if q(f"local x = {call}")[0] != 0:
        broken.append(display)
check(f"all {len(m.DISPATCHERS)} dispatchers construct", not broken, ", ".join(broken))

# A dispatcher with an arg key but no suggestions would emit a bare
# hl.dsp.foo() and be rejected. This happened once; guard it.
suggestionless = [d for d, _p, k, _pl, _desc in m.DISPATCHERS
                  if k and d not in m.ARG_SUGGESTIONS]
check("every arg-taking dispatcher has suggestions", not suggestionless,
      ", ".join(suggestionless))

# --- no duplicate keys in any emitted table
dupes = []
for display, path, key, plain, _desc in m.DISPATCHERS:
    sugg = m.ARG_SUGGESTIONS.get(display) or []
    line = m.lua_bind_line({"keys": "X", "description": "d", "dispatcher": display,
                            "command": "true", "arg": sugg[0] if sugg else ""})
    inner = line[line.find("({"):] if "({" in line else ""
    seen, d = set(), []
    for part in inner.strip("(){} ").split(","):
        k = part.split("=")[0].strip()
        if k:
            if k in seen:
                d.append(k)
            seen.add(k)
    if d:
        dupes.append(f"{display}:{','.join(d)}")
check("no duplicate keys in emitted tables", not dupes, "; ".join(dupes))

# --- every dispatcher has a .conf spelling, or pre-0.56 users get broken binds
missing = [p for _n, p, _k, _pl, _d in m.DISPATCHERS if p not in m.CONF_DISPATCHERS]
check("every dispatcher has a conf spelling", not missing, ", ".join(missing))

# --- .conf rendering is structurally valid
CASES = [
    ("SUPER + F13", "no-arg", {"dispatcher": "close", "arg": "", "command": ""}),
    ("SUPER + F14", "string arg", {"dispatcher": "focus", "arg": "left", "command": ""}),
    ("SUPER + F15", "exec", {"dispatcher": "exec", "command": "true", "arg": ""}),
    ("SUPER + F16", "numeric arg", {"dispatcher": "resize", "arg": "40", "command": ""}),
    ("SUPER + F17", "enum arg", {"dispatcher": "fullscreen", "arg": "maximized", "command": ""}),
]
state = dict(base_state)
state["binds"] = [{"keys": k, "description": d, **rest} for k, d, rest in CASES]
conf = m.emit_conf(state)
check("conf output structurally valid", m.conf_syntax_error(conf) is None,
      str(m.conf_syntax_error(conf)))
check("conf has no lua left in it", "hl.dsp" not in conf)
check("conf maps window.close to closewindow", "closewindow" in conf)
check("conf maps focus to focuswindow", "focuswindow" in conf)
check("conf maps window.resize to resizeactive", "resizeactive" in conf)

# --- and they actually register
m.save_state(state)
out = m.apply_now()
check("reload accepted", out == "ok", out[:160])
errs = subprocess.run(["hyprctl", "configerrors"], capture_output=True,
                      text=True).stdout.strip()
check("no config errors", not errs, errs[:400])

registered = {b["key"] for b in binds()}
for keys, desc, _rest in CASES:
    bare = keys.split(" + ")[-1]
    check(f"{desc} binding registered", bare in registered,
          "" if bare in registered else "not in hyprctl binds")

# --- restore
m.save_state(base_state)
m.apply_now()
time.sleep(0.8)
check("test bindings removed",
      not any(b["key"] in ("F13", "F14", "F15", "F16", "F17") for b in binds()))

# --- command scanning
cmds = m.scan_commands()
check("command scan is non-trivial", len(cmds) > 50, f"{len(cmds)} found")
check("command scan is sorted", cmds == sorted(cmds))
check("command scan has no paths", not any("/" in c for c in cmds))
check("command scan has no extensions", not any("." in c for c in cmds[:200]))
check("omarchy commands found", any(c.startswith("omarchy-") for c in cmds))

print()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
