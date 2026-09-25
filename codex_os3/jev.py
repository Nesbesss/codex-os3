"""Optional TypeSafe Jev advisor for the watchdog (https://docs.typesafe.ai/api).

Rules in watchdog.py decide; Jev adds a calibrated second opinion on the fuzzy part:
is OS3's silence after our last reply normal (it is waiting on the user, a worker or a
long command) or a symptom (dead tunnel, stuck task)? Only text goes to Jev: tool names,
timings, agent log lines — never message contents or screenshots."""
import json, urllib.request

URL = "https://api.typesafe.ai/v1/systemone"

DIAGNOSES = {
    "healthy": "Requests flow normally, or OS3 is idle with nothing pending.",
    "waiting_normally": "OS3 is legitimately waiting: on the user, a background worker, a schedule, "
                        "or a long-running command it started.",
    "tunnel_dead": "The router asked OS3 to run tools, the rabbit-agent kept working (ran them, or "
                   "aborted the task) but OS3 never sent the follow-up model request.",
    "agent_down": "The rabbit-agent process is not running or not connected.",
    "codex_unstable": "Model calls repeatedly hang or fail on the router side.",
    "usage_limit": "The model subscription's usage limit is reached.",
    "unclear": "The evidence does not support any of the above.",
}


def _state(s):
    resp = s.get("last_response") or {}
    lim = s.get("limits") or {}
    return {
        "router_last_reply": {"seconds_ago": resp.get("ago_s"), "kind": resp.get("result"),
                              "tool_calls_requested": resp.get("calls")},
        "seconds_since_any_request_from_os3": s.get("last_request_ago_s"),
        "rabbit_agent": {k: (s.get("agent") or {}).get(k) for k in ("status", "running")},
        "agent_commands_run_since_reply": s.get("agent_execs_since_response"),
        "agent_aborted_task_since_reply": s.get("agent_aborted_task_since_response"),
        "agent_log_recent": s.get("agent_log_recent", [])[-8:],
        "model_hangs_last_30_min": s.get("hangs_30m"),
        "errors_last_30_min": s.get("errors_30m"),
        "usage_limit_recent": bool(s.get("usage_limit")),
        "weekly_limit_used_percent": max([l.get("s_pct") or 0 for l in lim.values()], default=None),
    }


QUESTIONS = {
    "diagnosis": {
        "type": "choice",
        "instructions": "This is the health snapshot of a local LLM router used by rabbit OS3 through the "
                        "rabbit-agent tunnel. Which situation best describes it right now?",
        "criteria": DIAGNOSES,
    },
    "silence_expected": {
        "type": "noul",
        "instructions": "Is it normal that OS3 has not sent a new request since `router_last_reply`, given which "
                        "tool calls were requested (`router_last_reply.tool_calls_requested`) and how long ago?",
        "criteria": {"true": "Normal: the requested tools wait on a person, a worker, a schedule or a long command, "
                             "or the reply was a final answer.",
                     "false": "Not normal: quick tool calls were requested long ago and OS3 should already have "
                              "called back."},
    },
}


def advise(cfg, snapshot, timeout=8):
    """-> {"diagnosis", "confidence", "probabilities", "silence_expected"} or None on any error."""
    key = cfg.get("jev_key")
    if not key:
        return None
    body = {"state": _state(snapshot), "model": "jev-latest", "questions": QUESTIONS}
    req = urllib.request.Request(URL, json.dumps(body).encode(),
                                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            a = json.load(r)["answers"]
        d = a["diagnosis"]
        return {"diagnosis": d["choice"], "confidence": d.get("confidence"),
                "probabilities": d.get("probabilities"),
                "silence_expected": a["silence_expected"].get("noul")}
    except Exception:
        return None
