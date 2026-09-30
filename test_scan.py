import importlib.util
import os
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "g", str(Path(__file__).with_name("hypr_gui.py")))
m = importlib.util.module_from_spec(spec)
sys.argv = ["g"]
spec.loader.exec_module(m)

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails.append(name)


# --- _looks_like_command: real commands with dots are kept
for good in ("fsck.btrfs", "alsa-info.sh", "python3.12", "ldconfig.noconf",
             "gst-launch-1.0", "hypr-gui", "omarchy-capture-screenshot"):
    check(f"keeps real command {good}", m._looks_like_command(good))

# --- data and libraries are dropped
for bad in ("libfoo.so", "libfoo.so.1", "libc.so.6", "icon.png", "app.desktop",
            "config.json", "notes.txt", "font.ttf", ".hidden", "python3",
            "bash", "env", "yes", "test"):
    check(f"drops {bad}", not m._looks_like_command(bad))

# --- version stripping
check("strips -1.18", m._strip_version_suffix("aclocal-1.18") == "aclocal")
check("strips .12", m._strip_version_suffix("python3.12") == "python3")
check("leaves plain name", m._strip_version_suffix("hyprctl") is None)
check("leaves dash-name", m._strip_version_suffix("omarchy-menu") is None)

# --- .desktop scanning, against a synthetic tree
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    cases = {
        "plain.desktop":    "[Desktop Entry]\nExec=/usr/bin/totally-not-installed %U\n",
        "env.desktop":      "[Desktop Entry]\nExec=env FOO=bar /opt/thing/bin/otherapp --flag\n",
        "shwrap.desktop":   "[Desktop Entry]\nExec=/bin/sh -c \"some app\"\n",
        "noexec.desktop":   "[Desktop Entry]\nName=Nothing\n",
        "notanentry.txt":   "Exec=nope\n",
        "lib.desktop":      "[Desktop Entry]\nExec=/usr/lib/thing/libfoo.so\n",
        "flatpak.desktop":  "[Desktop Entry]\nExec=flatpak run com.example.App\n",
        "nohup.desktop":    "[Desktop Entry]\nExec=nohup /usr/bin/backgrounded-app\n",
    }
    for name, body in cases.items():
        (d / name).write_text(body)

    check("desktop: plain Exec gives the binary",
          m._desktop_exec(d / "plain.desktop") == "totally-not-installed",
          str(m._desktop_exec(d / "plain.desktop")))
    check("desktop: unwraps env and VAR= assignments",
          m._desktop_exec(d / "env.desktop") == "otherapp",
          str(m._desktop_exec(d / "env.desktop")))
    check("desktop: sh -c yields nothing runnable",
          m._desktop_exec(d / "shwrap.desktop") is None,
          str(m._desktop_exec(d / "shwrap.desktop")))
    check("desktop: flatpak run yields flatpak",
          m._desktop_exec(d / "flatpak.desktop") == "flatpak",
          str(m._desktop_exec(d / "flatpak.desktop")))
    check("desktop: unwraps nohup",
          m._desktop_exec(d / "nohup.desktop") == "backgrounded-app",
          str(m._desktop_exec(d / "nohup.desktop")))
    check("desktop: no Exec line gives None",
          m._desktop_exec(d / "noexec.desktop") is None)
    check("desktop: a library is not a command",
          not m._looks_like_command(m._desktop_exec(d / "lib.desktop") or ""))

    found = set()
    real_dirs = m._desktop_dirs
    m._desktop_dirs = lambda: [d]
    try:
        m._scan_desktop_entries(found)
    finally:
        m._desktop_dirs = real_dirs

    check("desktop: picks up Exec binary", "totally-not-installed" in found,
          str(sorted(found)))
    check("desktop: adds the unwrapped binary", "otherapp" in found,
          str(sorted(found)))
    check("desktop: adds the nohup'd binary", "backgrounded-app" in found,
          str(sorted(found)))
    check("desktop: never adds a wrapper or flag",
          "env" not in found and "nope" not in found and "-c" not in found,
          str(sorted(found)))

# --- shell alias scanning, against a synthetic rc file
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    (home / ".bashrc").write_text(
        "alias ll='ls -la'\n"
        "alias gs='git status'\n"
        "alias cd..='cd ..'\n"
        "alias bare=ls\n"
        "function myfn() { echo hi; }\n"
        "function spaced { echo hi; }\n"
        "# alias commented='x'\n"
        "  # alias indented='x'\n"
        "export PATH=$PATH:/nope\n"
        "alias\n"
        "function\n")
    found = set()
    saved_home = Path.home
    try:
        import pathlib
        pathlib.Path.home = staticmethod(lambda: home)
        m._scan_shell_functions(found)
    finally:
        pathlib.Path.home = saved_home
    # An alias IS runnable, so ll and cd.. belong in the list even though
    # their bodies have arguments. Only the paren in a function name is wrong.
    check("shell: finds simple alias", "gs" in found, str(sorted(found)))
    check("shell: keeps alias with args", "ll" in found, str(sorted(found)))
    check("shell: keeps dotted alias name", "cd.." in found, str(sorted(found)))
    check("shell: function name has no parens", "myfn" in found, str(sorted(found)))
    check("shell: no parens leaked", "myfn()" not in found, str(sorted(found)))
    check("shell: function without parens works", "spaced" in found, str(sorted(found)))
    check("shell: bare alias body works", "bare" in found, str(sorted(found)))
    check("shell: ignores commented alias", "commented" not in found)
    check("shell: ignores indented comment", "indented" not in found)
    check("shell: does not read export", "PATH" not in found)
    check("shell: bare 'alias' line adds nothing", "" not in found)

# --- no source can produce a path or a data file
cmds = m.scan_commands()
check("no path separators", not any("/" in c for c in cmds))
check("no library files", not any(c.lower().endswith(".so") for c in cmds))
check("no duplicates", len(cmds) == len(set(cmds)))
check("sorted", cmds == sorted(cmds))
check("no empty strings", "" not in cmds)
check("plausible size", 500 < len(cmds) < 60000, f"{len(cmds)}")

# --- cache actually works
cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "hypr-gui"
check("cache written", (cache / "commands.json").exists())

print()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
