"""Per-task export: everything needed to see what went wrong, secrets redacted."""
import glob, io, json, os, re, time, zipfile

from . import config, store

_PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1[REDACTED]"),
    (re.compile(r"\b(sk|cx|gho|ghp|xox[bap]|AIza)[-_A-Za-z0-9]{16,}"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)((?:pass(?:word)?|wachtwoord|pwd|secret|token|api[_-]?key)\s*[:=]\s*[\"']?)[^\s\"',}]{3,}"),
     r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:pass(?:word)?|wachtwoord)\"?\s*:\s*\")[^\"]+"), r"\1[REDACTED]"),
    (re.compile(r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]{100,}"), "[image omitted]"),
]


def redact(text, extra=()):
    for s in extra:
        if s and len(s) >= 6:
            text = text.replace(s, "[REDACTED]")
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


def tasks(limit=50):
    return store.q(
        "SELECT task, MIN(ts) AS start, MAX(COALESCE(done_ts, ts)) AS last, COUNT(*) AS requests, "
        "SUM(COALESCE(in_tok,0)) AS in_tok, SUM(COALESCE(cached_tok,0)) AS cached_tok, "
        "SUM(COALESCE(out_tok,0)) AS out_tok, SUM(status='error') AS errors, "
        "MAX(tools) AS tools FROM requests WHERE task IS NOT NULL GROUP BY task "
        "ORDER BY last DESC LIMIT ?", (limit,))


def _fmt(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"


def summarize(reqs, events):
    kinds = [e["kind"] for e in events]
    last = reqs[-1] if reqs else {}
    if any(r["status"] == "limit" for r in reqs):
        return "Codex usage limit reached."
    if "tunnel_dead" in kinds:
        return "The rabbit-agent's LLM tunnel died (OS3 stopped calling the router)."
    if last.get("status") == "error":
        return f"Last request failed: {last.get('error', '')[:200]}"
    if last.get("status") == "gone":
        return "OS3 hung up on the last request (timeout or cancelled)."
    if last.get("result") == "final":
        return "Task ended with a final answer."
    if last.get("result") == "tool_call":
        return "Router's last reply asked OS3 to run tools; no follow-up request was received."
    return "No conclusion."


def build(task, cfg=None):
    """-> zip bytes for one task."""
    cfg = cfg or config.load()
    secrets = [cfg.get("api_key", ""), cfg.get("jev_key", "")]
    reqs = store.q("SELECT * FROM requests WHERE task=? ORDER BY ts", (task,))
    if not reqs:
        raise KeyError(task)
    t0, t1 = reqs[0]["ts"] - 60, (reqs[-1]["done_ts"] or reqs[-1]["ts"]) + 900
    events = store.q("SELECT * FROM events WHERE (task=? OR (source='watchdog' AND ts BETWEEN ? AND ?)) "
                     "ORDER BY ts", (task, t0, t1))
    timeline = sorted([dict(r, type="request") for r in reqs] + [dict(e, type="event") for e in events],
                      key=lambda x: x["ts"])

    lines = [f"# codex-os3 task export `{task}`", "",
             f"**Summary:** {summarize(reqs, events)}", "",
             f"- requests: {len(reqs)}, errors: {sum(r['status'] == 'error' for r in reqs)}",
             f"- tokens: input {sum(r['in_tok'] or 0 for r in reqs):,} "
             f"(cached {sum(r['cached_tok'] or 0 for r in reqs):,}), output {sum(r['out_tok'] or 0 for r in reqs):,}",
             f"- first: {_fmt(reqs[0]['ts'])}, last: {_fmt(reqs[-1]['done_ts'] or reqs[-1]['ts'])}", "",
             "## Timeline", "", "| time | what | details |", "|---|---|---|"]
    for x in timeline:
        if x["type"] == "request":
            dur = f"{x['done_ts'] - x['ts']:.0f}s" if x["done_ts"] else "…"
            calls = ", ".join(json.loads(x["calls"])) if x.get("calls") else ""
            lines.append(f"| {_fmt(x['ts'])} | request ({x['mode'] or '?'}) | {x['status']} in {dur}, "
                         f"{x['msgs']} msgs, {x['imgs'] or 0} img → {x['result'] or ''} {calls} "
                         f"{('· ' + x['error'][:120]) if x.get('error') else ''} |")
        else:
            lines.append(f"| {_fmt(x['ts'])} | {x['source']}: {x['kind']} | {x['msg'][:200]} |")
    report = redact("\n".join(lines) + "\n", secrets)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.md", report)
        z.writestr("timeline.json", redact(json.dumps(timeline, indent=1, default=str), secrets))
        for f in sorted(glob.glob(os.path.join(config.HOME, "captures", task, "*.json"))):
            with open(f) as fh:
                z.writestr("captures/" + os.path.basename(f), redact(fh.read(), secrets))
    return buf.getvalue()
