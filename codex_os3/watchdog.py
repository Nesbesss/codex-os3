"""Watches the router and the local rabbit-agent and fixes what it safely can.

The failure it exists for: the rabbit-agent's LLM tunnel dies silently. The agent still
reports "connected" and still executes commands over its control channel, but OS3's
model requests never reach this router, so every task fails with "Local LLM device can't
be reached". Evidence: our last reply asked OS3 to run tools, the agent ran them (or
aborted the task with `act.py release_all`), yet no follow-up request ever arrived.
The fix is restarting the agent through its own scheduler (os3.restart_agent).

Rules decide; the optional Jev advisor (jev.py) only adds a second opinion for the
ambiguous "is this silence expected?" case and is recorded alongside."""
import json, time, urllib.request

from . import config, jev, os3, store

TICK_S = 15
QUIET_S = 120                 # silence after a quick tool call before we suspect the tunnel
RESTART_COOLDOWN_S = 600
DEDUPE_S = 600
# tool calls after which silence is normal: OS3 waits for the user, a worker, or a schedule
SLOW_TOOLS = {"wait", "ask_user", "create_task", "steer_task", "schedule_add", "schedule_update",
              "phone_call", "phone_call_status", "answer_worker_question"}


def last_response():
    r = store.q("SELECT * FROM requests WHERE status IN ('ok','limit') ORDER BY done_ts DESC LIMIT 1")
    return r[0] if r else None


def last_request_ts():
    r = store.q("SELECT MAX(ts) AS t FROM requests")
    return (r[0]["t"] or 0) if r else 0


def snapshot():
    now = time.time()
    resp = last_response()
    calls = json.loads(resp["calls"]) if resp and resp.get("calls") else []
    since = resp["done_ts"] if resp else now - 900
    agent_events = os3.log_tail(since_ts=since - 5, components=("exec", "ws", "tunnel", "tunnel-ws", "main"))
    execs = [e for e in agent_events if e.get("component") == "exec" and "completed" in e.get("message", "")
             and e["ts"] > since]
    aborts = [e["ts"] for e in agent_events if "release_all" in e.get("message", "") and e["ts"] > since]
    lim = store.q("SELECT * FROM limits ORDER BY ts DESC LIMIT 1")
    return {
        "now": now,
        "last_response": resp and {"id": resp["id"], "ago_s": round(now - resp["done_ts"]),
                                   "result": resp["result"], "calls": calls, "task": resp["task"]},
        "last_request_ago_s": round(now - last_request_ts()) if last_request_ts() else None,
        "agent": os3.status(),
        "agent_execs_since_response": len(execs),
        "agent_aborted_task_since_response": bool(aborts),
        "agent_abort_ago_s": round(now - aborts[0]) if aborts else None,
        "agent_log_recent": [f"{e['component']} {e.get('level')} {e.get('message', '')[:120]}"
                             for e in agent_events[-12:]],
        "hangs_30m": store.q("SELECT COUNT(*) n FROM events WHERE kind IN ('retry','fix_failed','verify_failed') "
                             "AND ts > ?", (now - 1800,))[0]["n"],
        "errors_30m": store.q("SELECT COUNT(*) n FROM events WHERE level='error' AND ts > ?", (now - 1800,))[0]["n"],
        "limits": lim[0] if lim else None,
        "usage_limit": store.kv_get("usage_limit"),
    }


def rules(s):
    """-> list of findings {kind, level, msg, action}."""
    out = []
    resp, agent = s["last_response"], s["agent"]
    waiting_for_os3 = (resp and resp["result"] == "tool_call"
                       and (s["last_request_ago_s"] or 0) >= resp["ago_s"] - 2)  # nothing came in since
    if waiting_for_os3 and agent.get("running"):
        quick = not (set(resp["calls"]) & SLOW_TOOLS)
        # a user cancelling also runs release_all, but then OS3 keeps talking to us (the chat
        # answers); a dead tunnel stays silent. Give it 45s before calling it dead.
        if s["agent_aborted_task_since_response"] and (s.get("agent_abort_ago_s") or 0) >= 45:
            out.append({"kind": "tunnel_dead", "level": "error", "action": "restart_agent",
                        "msg": "OS3 aborted the task after our tool call and never called back: "
                               "the rabbit-agent's LLM tunnel is likely dead"})
        elif quick and s["agent_execs_since_response"] and resp["ago_s"] > QUIET_S:
            out.append({"kind": "tunnel_dead", "level": "error", "action": "restart_agent",
                        "msg": f"the agent ran our tool calls ({', '.join(resp['calls'][:4])}) but no "
                               f"follow-up request for {resp['ago_s']}s: LLM tunnel likely dead"})
        elif quick and resp["ago_s"] > QUIET_S * 2:
            out.append({"kind": "stalled", "level": "warn", "action": None,
                        "msg": f"no follow-up for {resp['ago_s']}s after {', '.join(resp['calls'][:4])}"})
    if agent and not agent.get("running") and os3.installed():
        out.append({"kind": "agent_down", "level": "error", "action": "restart_agent",
                    "msg": "rabbit-agent is not running"})
    ul = s["usage_limit"]
    if ul and time.time() - ul["ts"] < 3600:
        out.append({"kind": "usage_limit", "level": "warn", "action": None,
                    "msg": "Codex usage limit reached" + (f", resets at {ul['resets']}" if ul.get("resets") else "")})
    lim = s["limits"]
    if lim and (lim.get("s_pct") or 0) >= 90:
        out.append({"kind": "weekly_limit_high", "level": "warn", "action": None,
                    "msg": f"weekly Codex limit at {lim['s_pct']:.0f}%"})
    if s["hangs_30m"] >= 3:
        out.append({"kind": "codex_unstable", "level": "warn", "action": None,
                    "msg": f"{s['hangs_30m']} codex hangs/failed retries in 30 min"})
    return out


def _recent(kind, within):
    r = store.q("SELECT MAX(ts) t FROM events WHERE source='watchdog' AND kind=?", (kind,))
    return r and r[0]["t"] and time.time() - r[0]["t"] < within


def notify(cfg, finding):
    if not cfg.get("webhook"):
        return
    try:  # ntfy-style: POST plain text to the URL
        urllib.request.urlopen(urllib.request.Request(
            cfg["webhook"], data=f"codex-os3: {finding['msg']}".encode(), method="POST"), timeout=10)
    except Exception:
        pass


def tick(cfg):
    s = snapshot()
    findings = rules(s)
    resp = s["last_response"] or {}
    pending = resp.get("result") == "tool_call" and resp.get("ago_s", 0) > 30
    advice = jev.advise(cfg, s) if cfg.get("jev_key") and (pending or findings) else None
    if advice:
        store.kv_set("watchdog_advice", dict(advice, ts=time.time()))
        for f in findings:  # Jev may escalate the ambiguous case; the hard signals still gate it
            if (f["kind"] == "stalled" and advice["diagnosis"] == "tunnel_dead"
                    and (advice.get("confidence") or 0) >= 0.9 and (advice.get("silence_expected") or 1) < 0.2):
                f.update(kind="tunnel_dead", level="error", action="restart_agent",
                         msg=f["msg"] + f" (Jev: tunnel_dead {advice['confidence']:.0%})")
    acted_for = store.kv_get("acted_for")
    for f in findings:
        if f["action"] and resp.get("id") and acted_for == resp.get("id"):
            continue  # already handled this stalled reply; don't restart again for it
        if _recent(f["kind"], DEDUPE_S) and f["action"] is None:
            continue
        acted = None
        if f["action"] == "restart_agent" and cfg.get("restart_agent"):
            if _recent("restart_agent", RESTART_COOLDOWN_S):
                acted = "skipped: restarted the agent less than 10 min ago"
            else:
                ok, msg = os3.restart_agent()
                store.event("restart_agent", msg, source="watchdog", level="info" if ok else "error")
                store.kv_set("acted_for", resp.get("id"))
                acted = msg
        store.event(f["kind"], f["msg"] + (f" → {acted}" if acted else ""), task=(s["last_response"] or {}).get("task"),
                    source="watchdog", level=f["level"], data={"advice": advice})
        if f["level"] == "error" or acted:
            notify(cfg, f)
    store.kv_set("watchdog_last", {"ts": s["now"], "findings": findings,
                                   "agent": s["agent"], "advice": advice})
    return findings


def loop(stop):
    last_prune = 0
    while not stop.is_set():
        cfg = config.load()
        try:
            if cfg.get("watchdog", True):
                tick(cfg)
            if time.time() - last_prune > 3600:
                store.prune(cfg["retention_days"])
                last_prune = time.time()
        except Exception as e:  # the watchdog must never die
            try:
                store.event("watchdog_error", f"{type(e).__name__}: {e}", source="watchdog", level="error")
            except Exception:
                pass
        stop.wait(TICK_S)
