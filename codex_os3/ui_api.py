"""JSON API behind the web UI. handle() -> (status, body, content_type)."""
import os, re, shutil, subprocess, time

from . import __version__, config, export, os3, store
from .platform_util import pid_alive

J = "application/json"
EDITABLE = {"model", "effort", "bind", "port", "captures", "retention_days", "jev_key",
            "webhook", "watchdog", "restart_agent", "max_codex", "max_images"}


MIN_CODEX = "0.155.0"  # older CLIs reject the current models ("requires a newer version of Codex")


def _ver(v):
    try:
        return tuple(int(x) for x in str(v).split("-")[0].split(".")[:3])
    except ValueError:
        return (999,)  # dev/fake builds: don't block


def codex_info():
    b = shutil.which("codex")
    info = {"path": b, "version": None, "logged_in": None}
    if not b:
        return info
    try:
        v = subprocess.run([b, "--version"], capture_output=True, text=True, timeout=15)
        info["version"] = (v.stdout or v.stderr).strip().split()[-1] if (v.stdout or v.stderr) else None
        s = subprocess.run([b, "login", "status"], capture_output=True, text=True, timeout=15)
        out = (s.stdout + s.stderr).lower()
        info["logged_in"] = s.returncode == 0 and "not logged in" not in out
        info["login_detail"] = (s.stdout + s.stderr).strip()[:200]
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def doctor(cfg):
    c = codex_info()
    a = os3.status()
    first = store.q("SELECT MIN(ts) t, MAX(ts) l, COUNT(*) n FROM requests")[0]
    return [
        {"check": "Codex CLI installed", "ok": bool(c["path"]), "detail": c["path"] or "npm i -g @openai/codex"},
        {"check": f"Codex CLI version ≥ {MIN_CODEX}", "ok": _ver(c["version"]) >= _ver(MIN_CODEX),
         "detail": (c["version"] or "?") + ("" if _ver(c["version"]) >= _ver(MIN_CODEX)
                                             else " — update: npm i -g @openai/codex@latest")},
        {"check": "Codex logged in (ChatGPT subscription)", "ok": bool(c["logged_in"]),
         "detail": c.get("login_detail") or "run: codex login"},
        {"check": "rabbit-agent installed on this machine", "ok": os3.installed(),
         "detail": "install the OS3 node on this machine first" if not os3.installed() else "~/.rabbit-agent"},
        {"check": "rabbit-agent connected", "ok": a.get("status") == "connected" and a.get("running"),
         "detail": f"{a.get('status')} pid {a.get('pid')} v{a.get('version')}" if a else "no status file"},
        {"check": "OS3 has called this router", "ok": bool(first["n"]),
         "detail": f"last request {time.strftime('%H:%M:%S', time.localtime(first['l']))}" if first["n"]
         else "not yet — save the connection in OS3 (Settings → API keys)"},
    ]


def usage(hours):
    since = time.time() - hours * 3600
    bucket = 3600 if hours <= 48 else 86400
    rows = store.q(
        f"SELECT CAST(ts/{bucket} AS INT)*{bucket} AS t, COUNT(*) n, SUM(COALESCE(in_tok,0)) i, "
        "SUM(COALESCE(cached_tok,0)) c, SUM(COALESCE(out_tok,0)) o, SUM(tools>0) agent "
        "FROM requests WHERE ts>? GROUP BY t ORDER BY t", (since,))
    tot = store.q("SELECT COUNT(*) n, SUM(COALESCE(in_tok,0)) i, SUM(COALESCE(cached_tok,0)) c, "
                  "SUM(COALESCE(out_tok,0)) o FROM requests WHERE ts>?", (since,))[0]
    return {"bucket": bucket, "series": rows, "total": tot}


def handle(method, path, data, q, cfg):
    if method == "GET" and path == "status":
        lim = store.q("SELECT * FROM limits ORDER BY ts DESC LIMIT 1")
        wd = store.kv_get("watchdog_last") or {}
        running = store.q("SELECT COUNT(*) n FROM requests WHERE status='running' AND ts > ?", (time.time() - 900,))[0]["n"]
        return 200, {"version": __version__, "time": time.time(), "limits": lim[0] if lim else None,
                     "usage_limit": store.kv_get("usage_limit"), "agent": os3.status(),
                     "watchdog": wd, "running": running, "model": cfg["model"],
                     "endpoint": f"http://localhost:{cfg['port']}/v1"}, J
    if method == "GET" and path == "usage":
        return 200, usage(float(q.get("hours", 24))), J
    if method == "GET" and path == "requests":
        rows = store.q("SELECT id, ts, done_ts, task, model, stream, tools, msgs, bytes, imgs, mode, status, "
                       "error, result, calls, in_tok, cached_tok, out_tok FROM requests ORDER BY ts DESC LIMIT ?",
                       (int(q.get("limit", 100)),))
        return 200, rows, J
    if method == "GET" and path == "events":
        rows = store.q("SELECT * FROM events WHERE (? = '' OR source = ?) ORDER BY ts DESC LIMIT ?",
                       (q.get("source", ""), q.get("source", ""), int(q.get("limit", 200))))
        return 200, rows, J
    if method == "GET" and path == "tasks":
        return 200, export.tasks(int(q.get("limit", 50))), J
    if method == "GET" and path == "export":
        task = q.get("task", "")
        if not re.fullmatch(r"[0-9a-f]{8,40}", task):
            return 400, {"error": "bad task id"}, J
        return 200, export.build(task, cfg), "application/zip"
    if method == "GET" and path == "doctor":
        return 200, doctor(cfg), J
    if method == "GET" and path == "config":
        c = dict(cfg)
        c["jev_key"] = bool(c.get("jev_key"))  # never echo third-party secrets
        return 200, c, J  # api_key is shown: the UI is local-only or key-authenticated
    if method == "POST" and path == "config":
        upd = {k: v for k, v in data.items() if k in EDITABLE}
        if "jev_key" in upd and upd["jev_key"] is True:
            upd.pop("jev_key")  # UI echoes the masked boolean back; keep the stored key
        new = config.save(upd)
        store.event("config", f"changed: {', '.join(sorted(upd))}", source="ui")
        restart = bool({"bind", "port"} & set(upd))
        return 200, {"ok": True, "restart_needed": restart, "model": new["model"]}, J
    if method == "POST" and path == "key/rotate":
        new = config.save({"api_key": config.new_key()})
        store.event("key_rotated", "API key rotated — update it in OS3", source="ui", level="warn")
        return 200, {"api_key": new["api_key"]}, J
    if method == "POST" and path == "agent/restart":
        ok, msg = os3.restart_agent()
        store.event("restart_agent", msg + " (from UI)", source="ui", level="info" if ok else "error")
        return 200, {"ok": ok, "message": msg}, J
    if method == "POST" and path == "reload":
        if not _supervisor_pid():
            return 409, {"error": "supervisor not running"}, J
        request_reload()
        return 200, {"ok": True}, J
    return 404, {"error": f"unknown endpoint {method} /api/{path}"}, J


def _supervisor_pid():
    try:
        with open(os.path.join(config.HOME, "supervisor.pid")) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if pid_alive(pid) else None


def request_reload():
    """Graceful worker swap; a file instead of SIGHUP so it works on Windows too."""
    open(os.path.join(config.HOME, "reload.request"), "w").close()

