"""Live test of OS3-shaped requests through a running router (real model calls — costs quota).
Plays OS3 for each role: the connection probe, the main chat, a worker (OS3's real worker tools
from bench/template.json, device work via the shell tool) and a background call.

  CODEX_OS3_HOME=/tmp/x CODEX_OS3_PORT=11499 python3 tests/live_agents.py claude-sonnet-5-low [model ...]
  ... live_agents.py --roles     # role routing on: uses the models set per role in the dashboard
"""
import json, os, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from codex_os3 import config  # noqa: E402

cfg = config.ensure_key()
BASE = f"http://127.0.0.1:{cfg['port']}"
H = {"Content-Type": "application/json", "Authorization": "Bearer " + cfg["api_key"]}
TPL = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench", "template.json")))
NODE = "11111111-2222-4333-8444-555555555555"  # the node id inside template.json


def fn(name, desc, props=None, req=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": list(req)}}}


PING = [fn("ping", "Connection test. Call it when asked.")]
CHAT_TOOLS = [
    fn("create_task", "Start a background worker for work on a device.",
       {"title": {"type": "string"}, "instructions": {"type": "string"}, "node_id": {"type": "string"}},
       ("title", "instructions")),
    fn("notify_before_act", "Tell the user in one line what you are about to do.", {"message": {"type": "string"}}, ("message",)),
]
BG_TOOLS = [fn("emit_facts", "Store durable facts about the user.",
               {"facts": {"type": "array", "items": {"type": "string"}}}, ("facts",))]


def post(model, messages, tools):
    t = time.time()
    r = json.load(urllib.request.urlopen(urllib.request.Request(
        BASE + "/v1/chat/completions", json.dumps({"model": model, "messages": messages, "tools": tools}).encode(), H),
        timeout=600))
    return r["choices"][0]["message"], time.time() - t


def calls(m):
    return [(c["function"]["name"], json.loads(c["function"]["arguments"] or "{}")) for c in m.get("tool_calls") or []]


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'} {name} {detail}", flush=True)
    return bool(cond)


def probe(model):
    msgs = [{"role": "user", "content": "Call the ping function now."}]
    m, t1 = post(model, msgs, PING)
    ok = check("probe ping", [c[0] for c in calls(m)] == ["ping"], f"{t1:.0f}s")
    if not ok:
        return False
    msgs += [m, {"role": "tool", "tool_call_id": m["tool_calls"][0]["id"], "content": "Done."},
             {"role": "user", "content": "Now call the ping function"}]
    m, t2 = post(model, msgs, PING)
    return check("probe ping again", [c[0] for c in calls(m)] == ["ping"], f"{t2:.0f}s content={str(m.get('content'))[:80]!r}")


def chat(model):
    msgs = [{"role": "system", "content": "You are the user's assistant in rabbit OS3. Device work (files, apps) "
             f"goes to a worker via create_task. The user's device: Bench-Mac (node_id {NODE})."},
            {"role": "user", "content": "Can you tidy up my Downloads folder on Bench-Mac? Move old installers to the trash."}]
    m, t = post(model, msgs, CHAT_TOOLS)
    names = [c[0] for c in calls(m)]
    return check("chat delegates", "create_task" in names or "notify_before_act" in names, f"{t:.0f}s calls={names}")


def worker(model):
    task = "How many files are in the Downloads folder on Bench-Mac? Use the shell to count them."
    msgs = [TPL["system"], {"role": "user", "content": [{"type": "text", "text": f"<task>{task}</task>"}]}]
    used_shell, bad, t0 = False, [], time.time()
    for step in range(8):
        m, _ = post(model, msgs, TPL["tools"])
        msgs.append({k: v for k, v in m.items() if v is not None or k == "content"})
        if not m.get("tool_calls"):
            final = m.get("content") or ""
            return check("worker shell task", used_shell and "17" in final and not bad,
                         f"{time.time() - t0:.0f}s steps={step + 1} bad={bad} final={final[:90]!r}")
        for c in m["tool_calls"]:
            name, a = c["function"]["name"], json.loads(c["function"]["arguments"] or "{}")
            if name == "shell":
                used_shell = True
                if a.get("node_id") != NODE:
                    bad.append(f"shell node_id {a.get('node_id')!r}")
                res = json.dumps({"exitCode": 0, "stdout": "17\n", "stderr": ""})
            elif name == "skill_view":
                res = TPL["skill"]
            elif name == "ask_user":
                res = "User response: yes, go ahead."
            elif name in ("todo", "notify_before_act"):
                res = "ok"
            else:
                bad.append(f"unexpected {name}")
                res = "Error: not available in this test."
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": res})
    return check("worker shell task", False, f"no final answer after 8 steps, bad={bad}")


def background(model):
    msgs = [{"role": "system", "content": "Extract durable facts about the user from the conversation and store them "
             "with emit_facts."},
            {"role": "user", "content": "Conversation:\nuser: my dog is called Bo and I live in Utrecht\nassistant: Nice!"}]
    m, t = post(model, msgs, BG_TOOLS)
    c = calls(m)
    facts = " ".join(map(str, c[0][1].get("facts", []))) if c else ""
    return check("background facts", c and c[0][0] == "emit_facts" and "Bo" in facts and "Utrecht" in facts,
                 f"{t:.0f}s facts={facts[:80]!r}")


def events_since(t):
    rows = json.load(urllib.request.urlopen(BASE + "/api/events?limit=200", timeout=10))
    rows = rows if isinstance(rows, list) else rows.get("events", [])
    return [f"{e['kind']}: {e['msg'][:100]}" for e in rows if e["ts"] >= t and e.get("level") in ("warn", "error")]


if __name__ == "__main__":
    models = [a for a in sys.argv[1:] if not a.startswith("--")] or [cfg["model"]]
    all_ok = True
    for model in models:
        print(f"=== {model}", flush=True)
        t = time.time()
        for f in (probe, chat, worker, background):
            try:
                all_ok &= f(model)
            except Exception as e:
                all_ok &= check(f.__name__, False, f"crash {e!r}"[:200])
        for e in events_since(t):
            print("  router:", e)
    print("ALL PASS" if all_ok else "SOME FAILED")
