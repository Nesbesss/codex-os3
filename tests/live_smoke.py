"""Live smoke test against a running service (uses real Codex calls — costs quota).

  CODEX_OS3_HOME=/tmp/x CODEX_OS3_PORT=11499 python3 -m codex_os3 serve &
  CODEX_OS3_HOME=/tmp/x CODEX_OS3_PORT=11499 python3 tests/live_smoke.py [--reload]
"""
import json, os, sys, threading, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from codex_os3 import config  # noqa: E402

cfg = config.ensure_key()
BASE = f"http://127.0.0.1:{cfg['port']}"
H = {"Content-Type": "application/json", "Authorization": "Bearer " + cfg["api_key"]}
WEATHER = [{"type": "function", "function": {"name": "get_weather", "description": "weather for a city",
            "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}}]


def post(body, raw=False, timeout=300):
    r = urllib.request.urlopen(urllib.request.Request(BASE + "/v1/chat/completions", json.dumps(body).encode(), H),
                               timeout=timeout)
    return r.read().decode() if raw else json.load(r)


def get(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=10))


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'} {name} {detail}")
    return cond


ok = True
ok &= check("health", get("/health")["status"] == "ok")
ok &= check("models", cfg["model"] in [m["id"] for m in get("/v1/models")["data"]])
t = time.time()
d = post({"model": cfg["model"], "messages": [{"role": "user", "content": "say exactly: new router ok"}]})
ok &= check("chat", "new router ok" in d["choices"][0]["message"]["content"].lower(),
            f"{time.time() - t:.0f}s usage={d['usage']}")
d = post({"model": cfg["model"], "tools": WEATHER, "messages": [{"role": "user", "content": "weather in Tokyo?"}]})
tc = d["choices"][0]["message"].get("tool_calls") or []
ok &= check("tool call", tc and tc[0]["function"]["name"] == "get_weather", json.dumps(tc)[:120])
s = post({"model": cfg["model"], "stream": True, "tools": WEATHER,
          "messages": [{"role": "user", "content": "weather in Tokyo AND in Paris, check both now"}]}, raw=True)
n = sum(1 for line in s.split("\n") if line.startswith("data: {") and '"tool_calls"' in line)
ok &= check("stream multi-call", n >= 1 and s.count('"get_weather"') >= 2)
# two-turn agent conversation: the second turn must resume the codex session
turn1 = [{"role": "system", "content": "You are a weather bot."}, {"role": "user", "content": "weather in Oslo?"}]
d1 = post({"model": cfg["model"], "tools": WEATHER, "messages": turn1})["choices"][0]["message"]
tc1 = (d1.get("tool_calls") or [{}])[0]
if tc1:
    turn2 = turn1 + [d1, {"role": "tool", "tool_call_id": tc1["id"], "content": "-3C, snow"}]
    d2 = post({"model": cfg["model"], "tools": WEATHER, "messages": turn2})["choices"][0]["message"]
    modes = [r["mode"] for r in get("/api/requests?limit=2")]
    ok &= check("resume turn", "snow" in (d2.get("content") or "").lower() and modes[0] == "resume",
                f"modes={modes} reply={(d2.get('content') or '')[:60]!r}")
else:
    ok &= check("resume turn", False, "turn 1 made no tool call")
st = urllib.request.urlopen(urllib.request.Request(BASE + "/api/status"), timeout=10)
ok &= check("ui status api", json.load(st).get("version") is not None)

if "--reload" in sys.argv:
    res = {}

    def slow():
        try:
            res["d"] = post({"model": cfg["model"], "messages": [{"role": "user", "content":
                            "Count slowly from 1 to 40 in words, one per line."}]})
        except Exception as e:
            res["e"] = e
    th = threading.Thread(target=slow)
    th.start()
    time.sleep(3)
    before = get("/health")["pid"]
    urllib.request.urlopen(urllib.request.Request(BASE + "/api/reload", b"{}",
                           {"X-Codex-OS3": "1", "Content-Type": "application/json"}), timeout=10)
    pids, t0 = set(), time.time()
    while time.time() - t0 < 25:
        try:
            pids.add(get("/health")["pid"])
        except Exception as e:
            ok &= check("no refused connection during reload", False, str(e))
            break
        time.sleep(0.2)
    th.join(300)
    ok &= check("in-flight request survived reload", "d" in res, str(res.get("e", ""))[:120])
    ok &= check("new worker took over", any(p != before for p in pids), f"pids={sorted(pids)}")

u = get("/api/usage?hours=1")["total"]
ok &= check("tokens recorded", (u["i"] or 0) > 0, json.dumps(u))
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
