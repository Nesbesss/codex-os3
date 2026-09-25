"""Desktop notifications for the few things worth interrupting for (a subscription at 90%+,
a switch to the fallback model). macOS and Linux notify from the service itself; the Windows
tray shows the same alerts from /api/status. Each key notifies at most once per 30 min."""
import shutil, subprocess, sys, time

from . import store


def desktop(msg, key=None, title="os3-router"):
    key = "notified:" + (key or msg)
    if time.time() - (store.kv_get(key) or 0) < 1800:
        return
    store.kv_set(key, time.time())
    alerts = (store.kv_get("alerts") or [])[-9:] + [{"ts": time.time(), "text": msg}]
    store.kv_set("alerts", alerts)
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e", f"display notification {_q(msg)} with title {_q(title)}"],
                           timeout=10, capture_output=True)
        elif sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, msg], timeout=10, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


def _q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
