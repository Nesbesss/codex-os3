"""Anonymous problem reports to the developer (a Discord channel), only after the user said yes
(share_reports). Sent: version, OS, a random install id and the event (errors, what Self fix
found and did, automatic repairs), passed through export.redact. Never message contents, names,
hostnames or keys. At most 10 per hour per install, in the background."""
import json, platform, threading, time, urllib.request, uuid

from . import __version__, config, store

URL = "https://discord.com/api/webhooks/1553142523109842974/uWcpqC1Kaz45t63k7-5Mu1q4mYCQ5yQLlZq7dOChydrSCX8vS3qaeuBNslEF-p09mW8k"
KINDS = {"selffix", "selffix_action", "codex_update", "update_failed", "restart_agent", "error", "fallback", "selftest"}
PER_HOUR = 10
ICON = {"error": "🔴", "warn": "🟠", "info": "🟢"}


def install_id():
    i = store.kv_get("install_id")
    if not i:
        i = uuid.uuid4().hex[:10]
        store.kv_set("install_id", i)
    return i


def maybe_send(kind, msg, level="info"):
    """Called for every event; sends the interesting ones when the user opted in."""
    if kind not in KINDS or not URL:
        return
    try:
        if config.load().get("share_reports") is not True:
            return
        now = time.time()
        sent = [t for t in (store.kv_get("report_times") or []) if now - t < 3600]
        if len(sent) >= PER_HOUR:
            return
        store.kv_set("report_times", sent + [now])
        from .export import redact
        text = (f"{ICON.get(level, '⚪')} **{kind}** · os3-router {__version__} · {platform.system()} "
                f"{platform.release()} · install `{install_id()}`\n{redact(msg)[:1700]}")
        threading.Thread(target=_post, args=(text,), daemon=True).start()
    except Exception:
        pass  # reporting must never break anything


def _post(text):
    try:
        urllib.request.urlopen(urllib.request.Request(
            URL, json.dumps({"username": "os3-router reports", "content": text,
                             "allowed_mentions": {"parse": []}}).encode(),
            {"Content-Type": "application/json", "User-Agent": "os3-router/" + __version__}), timeout=15)
    except Exception:
        pass
