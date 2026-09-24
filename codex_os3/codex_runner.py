"""Runs `codex exec` (fresh or resumed) and returns the agent's reply, token usage and
the account's rate limits."""
import json, os, re, subprocess, tempfile, threading, time

from . import config, platform_util, sessions

WORKDIR = os.path.join(config.HOME, "work")

# codex is used as a decision backend; the app executes tools. Its own agent features would
# make it act locally ("verify" in its read-only sandbox) instead of emitting tool calls.
DISABLED = ("computer_use", "browser_use", "browser_use_external", "in_app_browser",
            "multi_agent", "image_generation", "goals", "memories", "plugins", "apps", "hooks",
            "shell_tool", "unified_exec", "sleep_tool", "tool_suggest")

_slots = None
_slots_lock = threading.Lock()


class ClientGone(Exception):
    """The HTTP client hung up (e.g. OS3 timed out and retried)."""


class CodexHung(RuntimeError):
    """codex showed no activity for hang_idle_s (upstream stream stalled)."""


class UsageLimit(RuntimeError):
    """The subscription's usage limit is reached."""

    def __init__(self, msg, resets=""):
        super().__init__(msg)
        self.resets = resets


def slots(n):
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(n)
    return _slots


def split_model(model, default_effort):
    for e in ("-xhigh", "-high", "-medium", "-low", "-minimal"):
        if model.endswith(e):
            return model[:-len(e)], e[1:]
    return model, default_effort


def build_cmd(cfg, model, schema_file=None, image_files=(), resume=None):
    model, effort = split_model(model, cfg["effort"])
    cmd = [cfg.get("codex_bin") or "codex", "exec", *(["resume"] if resume else []), "--json",
           "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
           *[a for f in DISABLED for a in ("--disable", f)],
           *([] if resume else ["-s", "read-only", "-C", WORKDIR]), "-m", model,
           "-c", f"model_reasoning_effort={json.dumps(effort)}"]
    if schema_file:
        cmd += ["--output-schema", schema_file]
    if image_files:
        cmd += ["-i", *image_files]
    if resume:
        cmd.append(resume)
    # the prompt goes over stdin ("-"): prompts reach 500 KB (near macOS's argv limit) and
    # Windows' codex.cmd shim would mangle special characters in an argument
    return cmd + ["--", "-"]


RESET_RE = re.compile(r"try again (?:at|in) ([^.\"\\]+)", re.I)


def last_rate_limits(thread):
    """Newest rate_limits (5h primary / weekly secondary) from the session's rollout."""
    for f in sessions.rollout_files(thread):
        try:
            with open(f, "rb") as fh:
                fh.seek(max(0, os.path.getsize(f) - 400_000))
                tail = fh.read().decode(errors="ignore").splitlines()
        except OSError:
            continue
        for line in reversed(tail):
            if '"rate_limits"' in line:
                try:
                    e = json.loads(line)
                    return (e.get("payload") or {}).get("rate_limits")
                except ValueError:
                    continue
    return None


def run(cfg, prompt, model, schema=None, alive=lambda: True, images=(), resume=None, keep=False):
    """-> (text, usage, thread, rate_limits). Raises ClientGone, CodexHung, UsageLimit,
    RuntimeError."""
    os.makedirs(WORKDIR, exist_ok=True)
    tmp = []
    try:
        schema_file = None
        if schema:
            f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
            json.dump(schema, f)
            f.close()
            tmp.append(f.name)
            schema_file = f.name
        img_files = []
        for ext, data in images:
            f = tempfile.NamedTemporaryFile(suffix="." + ext, delete=False)
            f.write(data)
            f.close()
            tmp.append(f.name)
            img_files.append(f.name)
        cmd = build_cmd(cfg, model, schema_file, img_files, resume)

        sem = slots(cfg["max_codex"])
        while not sem.acquire(timeout=2):
            if not alive():
                raise ClientGone()
        try:
            out, err_lines, thread = _supervise(cfg, cmd, prompt, alive, resume)
        finally:
            sem.release()
    finally:
        for f in tmp:
            try:
                os.unlink(f)
            except OSError:
                pass

    text, err, usage = [], None, {}
    for line in out:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("type")
        if t == "thread.started":
            thread = e.get("thread_id") or thread
        elif t == "item.completed" and (e.get("item") or {}).get("type") == "agent_message":
            text.append(e["item"].get("text", ""))
        elif t == "turn.completed":
            usage = e.get("usage") or {}
        elif t in ("error", "turn.failed"):
            err = e.get("message") or json.dumps(e.get("error", ""))

    limits = last_rate_limits(thread) if thread else None
    if thread and not keep and not resume:  # one-off call: don't leave history behind
        for f in sessions.rollout_files(thread):
            try:
                os.unlink(f)
            except OSError:
                pass
    if not text:
        msg = err or ("\n".join(err_lines) or "no output from codex")[-600:]
        if "usage limit" in msg.lower():
            m = RESET_RE.search(msg)
            raise UsageLimit(msg, m.group(1).strip() if m else "")
        raise RuntimeError(msg)
    return "\n".join(text), usage, thread, limits


def _supervise(cfg, cmd, prompt, alive, thread):
    """Run codex, killing it when the client leaves or it stops showing activity
    (new stdout event or its rollout file growing)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", **platform_util.popen_group_kwargs())
    out, err = [], []

    def feed():
        try:
            p.stdin.write(prompt)
            p.stdin.close()
        except (OSError, ValueError):
            pass
    state = {"last": time.time(), "thread": thread}

    def read_out():
        for line in p.stdout:
            line = line.strip()
            if line.startswith("{"):
                out.append(line)
                state["last"] = time.time()
                if '"thread.started"' in line:
                    try:
                        state["thread"] = json.loads(line).get("thread_id") or state["thread"]
                    except ValueError:
                        pass

    def read_err():
        for line in p.stderr:
            err.append(line.rstrip())
            del err[:-50]

    readers = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True),
               threading.Thread(target=feed, daemon=True)]
    for r in readers:
        r.start()
    start, rollout = time.time(), None
    try:
        while p.poll() is None:
            time.sleep(1)
            if not alive():
                raise ClientGone()
            if rollout is None and state["thread"]:
                rollout = (sessions.rollout_files(state["thread"]) or [None])[0]
            if rollout:
                try:
                    state["last"] = max(state["last"], os.path.getmtime(rollout))
                except OSError:
                    pass
            now = time.time()
            if now - state["last"] > cfg["hang_idle_s"] or now - start > cfg["hang_max_s"]:
                raise CodexHung(f"codex idle {now - state['last']:.0f}s (total {now - start:.0f}s)")
    except BaseException:
        platform_util.kill_tree(p)  # wrapper + native codex child
        p.wait()
        raise
    for r in readers:
        r.join(timeout=5)
    return out, err, state["thread"]
