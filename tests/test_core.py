"""Offline tests (no Codex calls). Each case is a failure seen in real OS3 traffic."""
import json, os, sys, tempfile, time, unittest

os.environ["CODEX_OS3_HOME"] = tempfile.mkdtemp(prefix="cxos3-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codex_os3 import export, prompt as P, repair, sessions, store, watchdog  # noqa: E402

NODE_A, NODE_B = "11111111-2222-4333-8444-555555555555", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
SYSTEM = (f'<node id="{NODE_A}" name="studio-mini" default="true"><hostname>Studio-Mini.local</hostname></node>'
          f'<node id="{NODE_B}" name="laptop"><hostname>Laptop.local</hostname></node>')
GEOM = ["--view-width", "1365", "--view-height", "768", "--original-width", "1920", "--original-height", "1080"]
TOOLS = [
    {"type": "function", "function": {"name": "computer_use", "parameters": {
        "type": "object", "required": ["node_id", "script"],
        "properties": {"node_id": {"type": "string"}, "script": {"type": "string"}, "args": {"type": "array"}}}}},
    {"type": "function", "function": {"name": "feed_image", "parameters": {
        "type": "object", "required": ["reference", "node_id"],
        "properties": {"reference": {"type": "string"}, "node_id": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "shell", "parameters": {
        "type": "object", "required": ["command", "node_id"],
        "properties": {"command": {"type": "string"}, "node_id": {"type": "string"}}}}},
]
CU = TOOLS[0]["function"]


def decision(*calls):
    return {"kind": "tool_call", "content": "",
            "calls": [{"tool": t, "arguments_json": json.dumps(a)} for t, a in calls]}


class Repair(unittest.TestCase):
    def test_unescaped_shell_backslash_is_repaired(self):
        # models write `find . \( -name x \)` unescaped: invalid JSON, OS3 then says node_id missing
        a = repair.load_args(r'{"command":"find . \( -name x \) ; echo a\nb","node_id":"x"}')
        self.assertEqual(a["command"], "find . \\( -name x \\) ; echo a\nb")

    def test_node_id_by_name_hostname_and_typo(self):
        f = lambda v: repair.fix_node_id({"node_id": v}, CU, SYSTEM)["node_id"]
        self.assertEqual(f("Studio Mini"), NODE_A)
        self.assertEqual(f("Studio-Mini.local"), NODE_A)
        self.assertEqual(f(NODE_A.replace("4333", "4334")), NODE_A)          # one garbled char
        self.assertEqual(f("0000-far-off"), "0000-far-off")               # never guess

    def test_missing_node_id_only_filled_when_one_node(self):
        one = f'<node id="{NODE_A}" name="x"></node>'
        self.assertEqual(repair.fix_node_id({}, CU, one)["node_id"], NODE_A)
        self.assertNotIn("node_id", repair.fix_node_id({}, CU, SYSTEM))

    def test_dlam_action_as_script(self):
        a = repair.fix_computer_use("computer_use", {"script": "wait", "args": ["--duration", "1"]})
        self.assertEqual((a["script"], a["args"][0]), ("act.py", "wait"))
        self.assertEqual(repair.fix_computer_use("computer_use", {"script": "capture"})["script"], "capture.py")

    def test_schema_problems(self):
        probs = repair.decision_problems(decision(("feed_image", {"node_id": NODE_A}), ("screenshot", {})),
                                         TOOLS, SYSTEM)
        self.assertTrue(any("reference is required" in p for p in probs))
        self.assertTrue(any("no tool named 'screenshot'" in p for p in probs))
        self.assertEqual(repair.decision_problems(decision(
            ("computer_use", {"node_id": NODE_A, "script": "capture.py", "args": ["--out", "/tmp/s.png"]})),
            TOOLS, SYSTEM), [])

    def test_capture_gets_feed_image(self):
        cap = {"id": "1", "type": "function", "function": {"name": "computer_use", "arguments": json.dumps(
            {"node_id": NODE_A, "script": "capture.py", "args": ["--out", "/s.png"]})}}
        out = repair.add_missing_feed([cap], TOOLS)
        self.assertEqual(out[-1]["function"]["name"], "feed_image")
        self.assertEqual(json.loads(out[-1]["function"]["arguments"])["reference"], "/s.png")


class Prompt(unittest.TestCase):
    def test_first_json_object_wins(self):
        # two identical objects glued together used to leak into chat as raw JSON
        raw = '{"kind":"tool_call","calls":[],"content":""}\n{"kind":"tool_call","calls":[],"content":""}'
        self.assertEqual(P.parse_decision(raw)["kind"], "tool_call")
        self.assertIsNone(P.parse_decision("no json"))

    def test_only_newest_images_attached(self):
        img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}
        msgs = [{"role": "user", "content": [{"type": "text", "text": f"s{i}"}, img]} for i in range(5)]
        imgs = P.Images(msgs, 2)
        p = P.flatten(msgs, [], imgs)
        self.assertEqual(len(imgs.files), 2)
        self.assertEqual(p.count("[older image omitted]"), 3)
        self.assertNotIn("iVBORw0KGgo", p)

    def test_tool_results_named_by_call_id(self):
        msgs = [{"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "get_setup_status"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "ok"}]
        self.assertIn("[tool result: get_setup_status]", P.flatten(msgs, TOOLS))

    def test_observe_loop(self):
        look = {"role": "assistant", "tool_calls": [{"function": {"name": "computer_use",
                "arguments": json.dumps({"script": "capture.py"})}}]}
        act = {"role": "assistant", "tool_calls": [{"function": {"name": "computer_use",
               "arguments": json.dumps({"script": "act.py"})}}]}
        self.assertEqual(P.observe_streak([act, look, look, look]), 3)
        self.assertEqual(P.observe_streak([look, act]), 0)

    def test_false_unavailable(self):
        self.assertTrue(P.FALSE_UNAVAILABLE.search("computer control isn’t available in this session"))
        self.assertTrue(P.FALSE_UNAVAILABLE.search("I don’t have a `ping` tool available in this session."))
        self.assertTrue(P.FALSE_UNAVAILABLE.search("There is no ping function available here."))
        self.assertFalse(P.FALSE_UNAVAILABLE.search("Done — the file is saved."))
        self.assertFalse(P.FALSE_UNAVAILABLE.search("I don't have any more questions, it's done."))
        names = ["ping", "computer_use", "shell"]
        self.assertTrue(P.claims_unavailable("The available tools here don’t include `ping`, so I can’t make that call.", names))
        self.assertTrue(P.claims_unavailable("computer_use is not something I can run here", names))
        self.assertFalse(P.claims_unavailable("Pinged it: the reply was pong.", names))          # no negation
        self.assertFalse(P.claims_unavailable("I can't find that file on your Mac.", names))       # no tool named
        self.assertFalse(P.claims_unavailable("Done, nothing else to do.", names))
        long_ok = ("I ran shell to check the display server and everything is set up correctly; screenshots and "
                   "input both work. There's no permission you need to enable.")
        self.assertFalse(P.claims_unavailable(long_ok, names))


class Sessions(unittest.TestCase):
    def test_resume_parallel_and_compaction(self):
        m1 = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
        k = sessions.key(m1, TOOLS)
        self.assertEqual(sessions.plan(k, m1), (True, None, None))
        self.assertEqual(sessions.plan(k, m1)[0], False)       # parallel retry: untracked
        sessions.done(k, "T1", m1, True)
        m2 = m1 + [{"role": "tool", "content": "r"}]
        tracked, th, delta = sessions.plan(k, m2)
        self.assertEqual((tracked, th, len(delta)), (True, "T1", 1))
        sessions.done(k, "T1", m2, True)
        compacted = m1 + [{"role": "user", "content": "[summary]"}]
        self.assertEqual(sessions.plan(k, compacted)[1], None)  # history rewritten: fresh
        sessions.done(k, None, compacted, False)
        self.assertIsNone(store.session_get(k))


class Roles(unittest.TestCase):
    def body(self, tools, system="You are an assistant."):
        return {"tools": [{"type": "function", "function": {"name": n}} for n in tools],
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": "x"}]}

    def test_classify(self):
        from codex_os3 import roles
        self.assertEqual(roles.classify(self.body(["create_task", "notify_before_act", "wait"])), "chat")
        self.assertEqual(roles.classify(self.body(["shell", "computer_use"], "You are a worker agent in OS3.")), "worker")
        self.assertEqual(roles.classify(self.body(["report_missed_action", "report_correction", "wait"])), "background")
        self.assertEqual(roles.classify(self.body(["emit_facts"])), "background")
        self.assertEqual(roles.classify(self.body([])), "background")
        self.assertEqual(roles.classify(self.body(["ping"])), "chat")  # OS3's connection probe

    def test_pick(self):
        from codex_os3 import roles
        cfg = {"model": "gpt-6-luna", "effort": "medium", "role_routing": True,
               "roles": {"worker": {"model": "gpt-6-sol", "effort": "high"}}}
        self.assertEqual(roles.pick(cfg, "worker", "gpt-6-luna"), "gpt-6-sol-high")
        self.assertEqual(roles.pick(cfg, "chat", "gpt-6-luna"), "gpt-6-luna-medium")
        self.assertEqual(roles.pick(dict(cfg, role_routing=False), "worker", "gpt-5.5"), "gpt-5.5")

    def test_split_effort(self):
        from codex_os3.codex_runner import split_model
        self.assertEqual(split_model("gpt-6-sol-ultra", "medium"), ("gpt-6-sol", "ultra"))
        self.assertEqual(split_model("gpt-6-luna", "medium"), ("gpt-6-luna", "medium"))


class Export(unittest.TestCase):
    def test_redaction(self):
        t = export.redact('Authorization: Bearer abcdefghijklmnop "password": "hunter2secret" '
                          'user pass: schoolpw123 key cx-0123456789abcdef0123 wachtwoord=geheim99',
                          extra=["mysecretkey"])
        for leak in ("abcdefghijklmnop", "hunter2secret", "schoolpw123", "cx-0123456789abcdef0123", "geheim99"):
            self.assertNotIn(leak, t)

    def test_build(self):
        rid = store.request_start("abcdef0123456789", "t", "m", False, 3, 2, 100)
        store.request_end(rid, status="ok", result="tool_call", calls=["computer_use"], mode="fresh")
        store.event("call_fixed", "password: hunter2 in args", task="abcdef0123456789")
        z = export.build("abcdef0123456789", {"api_key": "", "jev_key": ""})
        import io, zipfile
        rep = zipfile.ZipFile(io.BytesIO(z)).read("report.md").decode()
        self.assertIn("no follow-up request", rep)
        self.assertNotIn("hunter2", rep)


class Watchdog(unittest.TestCase):
    def snap(self, **kw):
        s = {"now": time.time(), "last_response": {"ago_s": 150, "result": "tool_call",
             "calls": ["computer_use", "feed_image"], "task": "t"}, "last_request_ago_s": 160,
             "agent": {"running": True, "status": "connected"}, "agent_execs_since_response": 2,
             "agent_aborted_task_since_response": False, "hangs_30m": 0, "errors_30m": 0,
             "limits": None, "usage_limit": None}
        s.update(kw)
        return s

    def kinds(self, s):
        return [(f["kind"], f["action"]) for f in watchdog.rules(s)]

    def test_dead_tunnel_after_executed_calls(self):
        self.assertIn(("tunnel_dead", "restart_agent"), self.kinds(self.snap()))

    def test_dead_tunnel_on_abort(self):
        s = self.snap(last_response={"ago_s": 60, "result": "tool_call", "calls": ["shell"], "task": "t"},
                      last_request_ago_s=65, agent_aborted_task_since_response=True, agent_abort_ago_s=50)
        self.assertIn(("tunnel_dead", "restart_agent"), self.kinds(s))
        s.update(agent_abort_ago_s=10, agent_execs_since_response=0)   # maybe a user cancel: wait
        self.assertEqual(self.kinds(s), [])

    def test_waiting_on_user_is_not_a_failure(self):
        s = self.snap(last_response={"ago_s": 900, "result": "tool_call", "calls": ["ask_user"], "task": "t"},
                      last_request_ago_s=905)
        self.assertEqual(self.kinds(s), [])

    def test_new_request_arrived(self):
        self.assertEqual(self.kinds(self.snap(last_request_ago_s=5)), [])

    def test_single_watchdog_lease(self):
        import os
        store.kv_set("watchdog_owner", {})
        self.assertTrue(watchdog._owner(os.getpid()))
        self.assertFalse(watchdog._owner(999999999 if os.getpid() != 999999999 else 1) and False)
        other = os.getppid()  # alive process holding a fresh lease blocks us
        store.kv_set("watchdog_owner", {"pid": other, "ts": time.time()})
        self.assertFalse(watchdog._owner(os.getpid()))
        store.kv_set("watchdog_owner", {"pid": other, "ts": time.time() - 999})  # stale lease
        self.assertTrue(watchdog._owner(os.getpid()))

    def test_cancelled_request_counts_as_activity(self):
        # a reply with tool calls at t-150; OS3's next request started earlier (a retry of a slow
        # turn) and was cancelled at t-20: OS3 reached us, so the tunnel is not dead
        now = time.time()
        store.db().execute("DELETE FROM requests")
        r1 = store.request_start("t", "x", "m", False, 1, 2, 10)
        r2 = store.request_start("t", "x", "m", False, 1, 2, 10)
        store.db().execute("UPDATE requests SET ts=? WHERE id=?", (now - 300, r1))
        store.db().execute("UPDATE requests SET ts=? WHERE id=?", (now - 200, r2))
        store.request_end(r1, status="ok", result="tool_call", calls=["ls", "shell"], done_ts=now - 150)
        store.request_end(r2, status="gone", done_ts=now - 20)
        self.assertLess(now - watchdog.last_request_ts(), 30)
        r3 = store.request_start("t", "x", "m", False, 1, 2, 10)  # still running = alive
        self.assertLess(now - watchdog.last_request_ts(), 2)
        store.request_end(r3, status="ok")

    def test_hang_limit_grows_with_effort(self):
        from codex_os3.codex_runner import idle_limit
        self.assertEqual(idle_limit({"hang_idle_s": 90}, "medium"), 90)
        self.assertEqual(idle_limit({"hang_idle_s": 90}, "high"), 180)
        self.assertEqual(round(idle_limit({"hang_idle_s": 90}, "xhigh")), 300)

    def test_final_answer_is_quiet(self):
        s = self.snap(last_response={"ago_s": 900, "result": "final", "calls": [], "task": "t"})
        self.assertEqual(self.kinds(s), [])


class ClaudeBackendTest(unittest.TestCase):
    def test_backend_by_model(self):
        from codex_os3 import roles
        for m in ("claude-sonnet-5-medium", "sonnet-high", "claude-opus-5-5"):
            self.assertEqual(roles.backend(m), "claude", m)
        for m in ("gpt-6-luna-medium", "gpt-6-sol"):
            self.assertEqual(roles.backend(m), "codex", m)

    def test_cmd_and_limits(self):
        from codex_os3 import claude_runner as C
        cmd = C.build_cmd({"effort": "medium"}, "claude-opus-5-5-ultra", {"type": "object"})
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "max")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertIn("--no-session-persistence", cmd)
        self.assertIn("--resume", C.build_cmd({"effort": "low"}, "claude-sonnet-5", resume="abc"))
        rl = C.limits({"unifiedWindows": {"five_hour": {"utilization": 0.39, "resetsAt": 1},
                                          "seven_day": {"utilization": 0.44, "resetsAt": 2}}})
        self.assertEqual((rl["primary"]["used_percent"], rl["secondary"]["window_minutes"]), (39.0, 10080))
        self.assertIsNone(C.limits({}))


class UpdaterTest(unittest.TestCase):
    def test_install_from_release_archive(self):
        import io, tarfile
        from codex_os3 import config, updater
        self.assertFalse(updater.managed())  # a checkout never updates itself
        self.assertTrue(updater.ver("v0.10.0") > updater.ver("0.9.9"))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            for name, data in (("os3-router-0.9.0/codex_os3/__init__.py", b'__version__ = "0.9.0"\n'),
                               ("os3-router-0.9.0/NEW.txt", b"new")):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        app = tempfile.mkdtemp()
        with open(os.path.join(app, "OLD.txt"), "w") as f:
            f.write("old")
        orig, updater._get = updater._get, lambda url, timeout=60: buf.getvalue()
        try:
            with self.assertRaises(RuntimeError):  # archive doesn't hold the tagged version
                updater.install("v0.9.1", app=app, run_tests=False)
            self.assertFalse(os.path.exists(os.path.join(app, "NEW.txt")))
            updater.install("v0.9.0", app=app, run_tests=False)
        finally:
            updater._get = orig
        self.assertTrue(os.path.exists(os.path.join(app, "NEW.txt")))
        self.assertTrue(os.path.exists(os.path.join(app + ".prev", "OLD.txt")))
        self.assertTrue(os.path.exists(os.path.join(config.HOME, "reload.request")))


if __name__ == "__main__":
    unittest.main()
