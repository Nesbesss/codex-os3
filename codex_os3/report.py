"""Anonymous problem reports to the developer (a Discord channel), only after the user said yes
(share_reports). Sent: version, OS, a random install id and the event (errors, what Self fix
found and did, automatic repairs), passed through export.redact. Never message contents, names,
hostnames or keys. At most 10 per hour per install, in the background."""
import json, os, platform, threading, time, urllib.request, uuid

from . import __version__, config, store


def _url():
    return os.environ.get("CODEX_OS3_REPORT_WEBHOOK", "")


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
    if kind not in KINDS or not _url():
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


def user_report(text, diagnostics=True):
    """"Report a problem" from the dashboard: sent because the user asked, whatever share_reports
    says. -> (ok, message)."""
    text = (text or "").strip()
    if not text:
        return False, "nothing to send"
    if not _url():
        return False, "problem reporting is not configured"
    now = time.time()
    sent = [t for t in (store.kv_get("user_report_times") or []) if now - t < 3600]
    if len(sent) >= 5:
        return False, "you sent 5 reports this hour; please try again later"
    store.kv_set("user_report_times", sent + [now])
    from .export import redact
    body = f"📝 **user_report** · os3-router {__version__} · {platform.system()} {platform.release()} · install `{install_id()}`\n>>> {redact(text)[:1200]}"
    if diagnostics:
        from . import onboarding
        cfg = config.load()
        steps = "; ".join(f"{s['id']}={s['state']}" for s in onboarding.status(cfg)["steps"])
        errs = store.q("SELECT kind, msg FROM events WHERE level IN ('warn','error') AND ts > ? ORDER BY ts DESC LIMIT 6",
                       (now - 86400,))
        body += (f"\n**setup:** {steps}\n**recent problems:**\n" +
                 ("\n".join(f"- {e['kind']}: {redact(e['msg'])[:140]}" for e in errs) or "- none"))
    threading.Thread(target=_post, args=(body[:1990],), daemon=True).start()
    store.event("user_report", text[:200], source="ui")
    return True, "Sent. Thank you!"


def _post(text):
    try:
        urllib.request.urlopen(urllib.request.Request(
            _url(), json.dumps({"username": "os3-router reports", "content": text,
                             "allowed_mentions": {"parse": []}}).encode(),
            {"Content-Type": "application/json", "User-Agent": "os3-router/" + __version__}), timeout=15)
    except Exception:
        pass
