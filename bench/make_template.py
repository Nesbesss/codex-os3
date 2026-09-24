"""Build bench/template.json from a captured OS3 computer-use request (captures on)."""
import json, os, re, sys

NODE = "11111111-2222-4333-8444-555555555555"
d = json.load(open(sys.argv[1]))["request"]
msgs, ids = d["messages"], {}
for m in msgs:
    for c in m.get("tool_calls") or []:
        ids[c["id"]] = c["function"]["name"]
res = {}
for m in msgs:
    if m.get("role") == "tool" and ids.get(m.get("tool_call_id")) in ("skill_view", "computer_use_prepare"):
        t = m["content"] if isinstance(m["content"], str) else "".join(p.get("text", "") for p in m["content"])
        res.setdefault(ids[m["tool_call_id"]], t)
if len(res) < 2:
    sys.exit("capture needs both a skill_view(dlam) and a computer_use_prepare result")
real = re.search(r'<node id="([^"]+)"', json.dumps(msgs[0]))
home = os.path.expanduser("~")
fix = lambda s: s.replace(real.group(1) if real else "-", NODE).replace(home, "~")
system = ("You are a worker agent in OS3. You execute tasks by calling tools on the user's device.\n"
          f'<nodes>\n<node id="{NODE}" name="Bench-Mac" default="true">\n<can-run-commands>yes</can-run-commands>\n'
          "<platform>darwin</platform>\n<hostname>Bench-Mac.local</hostname>\n</node>\n</nodes>\n"
          "The user has approved operating the device for this task. Use the dlam skill (skill_view name=dlam) "
          "and computer_use for anything on screen. Report the result when done.")
out = {"system": {"role": "system", "content": system}, "tools": json.loads(fix(json.dumps(d["tools"]))),
       "skill": fix(res["skill_view"]), "prepare": fix(res["computer_use_prepare"])}
json.dump(out, open(os.path.join(os.path.dirname(__file__), "template.json"), "w"))
print("bench/template.json written")
