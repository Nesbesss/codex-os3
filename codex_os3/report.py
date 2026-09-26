"""Opt-in problem reports through the public report intake. No webhook secret is shipped."""
import json, os, platform, re, threading, time, urllib.request, uuid

from . import __version__, config, store

DEFAULT_ENDPOINT = "https://os3-router-report-intake.vercel.app/api/report"
KINDS = {"selffix", "selffix_action", "codex_update", "update_failed", "restart_agent", "error", "fallback", "selftest"}
PER_HOUR = 10


def _url():
    return os.environ.get("CODEX_OS3_REPORT_ENDPOINT", DEFAULT_ENDPOINT)


def install_id():
    i = store.kv_get("install_id")
    if not i:
        i = uuid.uuid4().hex[:10]
        store.kv_set("install_id", i)
    return i


def _message(text, limit):
    from .export import redact
    clean = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", redact(str(text or "")))
    return "\n".join(clean.splitlines()[:20]).strip()[:limit]


def _payload(kind, message, level="info"):
    system = re.sub(r"[^A-Za-z0-9._ -]", "", f"{platform.system()} {platform.release()}")[:80].strip() or "Unknown"
    return {"kind": kind, "level": level if level in {"info", "warn", "error"} else "info",
            "version": __version__, "os": system, "installId": install_id(), "message": message}


def maybe_send(kind, msg, level="info"):
    """Send selected events only when the user opted in; never block the router."""
    if kind not in KINDS or not _url():
        return
    try:
        if config.load().get("share_reports") is not True:
            return
        now = time.time()
        sent = [t for t in (store.kv_get("report_times") or []) if now - t < 3600]
        if len(sent) >= PER_HOUR:
            return
        message = _message(msg, 1500)
        if not message:
            return
        store.kv_set("report_times", sent + [now])
        threading.Thread(target=_post, args=(_payload(kind, message, level),), daemon=True).start()
    except Exception:
        pass  # reporting must never break anything


def user_report(text, diagnostics=True):
    """Queue an explicit dashboard report, regardless of automatic-report opt-in."""
    text = (text or "").strip()
    if not text:
        return False, "nothing to send"
    if not _url():
        return False, "problem reporting is not configured"
    now = time.time()
    sent = [t for t in (store.kv_get("user_report_times") or []) if now - t < 3600]
    if len(sent) >= 5:
        return False, "you sent 5 reports this hour; please try again later"
    message = _message(text, 1200)
    if diagnostics:
        from . import onboarding
        cfg = config.load()
        steps = "; ".join(f"{s['id']}={s['state']}" for s in onboarding.status(cfg)["steps"])
        errs = store.q("SELECT kind, msg FROM events WHERE level IN ('warn','error') AND ts > ? ORDER BY ts DESC LIMIT 6",
                       (now - 86400,))
        message += (f"\nsetup: {steps}\nrecent problems:\n" +
                    ("\n".join(f"- {e['kind']}: {_message(e['msg'], 140)}" for e in errs) or "- none"))
    message = _message(message, 1500)
    store.kv_set("user_report_times", sent + [now])
    threading.Thread(target=_post, args=(_payload("user_report", message),), daemon=True).start()
    store.event("user_report", text[:200], source="ui")
    return True, "Report queued. Thank you!"


def _post(payload):
    try:
        with urllib.request.urlopen(urllib.request.Request(
            _url(), json.dumps(payload).encode(),
            {"Content-Type": "application/json", "User-Agent": "os3-router/" + __version__}), timeout=15):
            pass
    except Exception:
        pass
