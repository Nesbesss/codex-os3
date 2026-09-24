"""Turning an OpenAI chat request into one codex prompt, and the nudges the engine appends."""
import base64, json, re

FALSE_UNAVAILABLE = re.compile(
    r"(n[\u2019']?t|not|no longer) (be )?(available|accessible|enabled|connected)|unavailable|"
    r"(no|without) access to|(couldn[\u2019']?t|could not|cannot|can[\u2019']?t) (access|reach|use|control)|"
    r"(do|does)(n[\u2019']?t| not) have (an? |the |any )?`?[\w.-]*`? ?(tool|function|access)|"
    r"(there is |there[\u2019']s )?no `?[\w.-]+`? (tool|function) (available|here|in this)",
    re.I)
_NEG = re.compile(r"\b(can[\u2019']?t|cannot|unable|don[\u2019']?t|doesn[\u2019']?t|not|no|isn[\u2019']?t|aren[\u2019']?t|without)\b", re.I)


def claims_unavailable(content, tool_names):
    """A final answer that says a tool/device is unavailable: known phrasings, or naming one of
    the tools that ARE available next to a negation ("the tools don't include `ping`")."""
    if FALSE_UNAVAILABLE.search(content or ""):
        return True
    if not content or not _NEG.search(content):
        return False
    return any(re.search(r"(?<![\w-])`?" + re.escape(n) + r"`?(?![\w-])", content) for n in tool_names if len(n) > 2)


RETRY_NUDGE = (
    "\n\nCORRECTION: your previous draft answered that a tool, device or computer control "
    "is unavailable. That is wrong: the application's tools are not in your built-in tool list; "
    "you call them by answering with kind=\"tool_call\" and the tool name in `calls`. Every "
    "application tool listed above is available and connected, and "
    "a successful tool result means it works. Do not report unavailability. Continue the "
    "task now with tool calls. Only give a final answer if a tool call above actually "
    "returned an error, and then quote that error verbatim.")


OBSERVE_SCRIPTS = ("capture.py", "probe.py")
LOOP_LIMIT = 3


def observe_streak(messages):
    """How many of the latest assistant turns only looked at the screen (capture/probe/
    feed_image/wait) without acting. Models get stuck 'verifying' forever."""
    n = 0
    for m in reversed(messages):
        calls = m.get("tool_calls") if m.get("role") == "assistant" else None
        if m.get("role") != "assistant":
            continue
        if not calls:
            break
        for c in calls:
            fn = c.get("function", {})
            name = fn.get("name", "")
            try:
                script = (json.loads(fn.get("arguments") or "{}") or {}).get("script", "")
            except ValueError:
                script = ""
            if not (name in ("feed_image", "wait") or script in OBSERVE_SCRIPTS):
                return n
        n += 1
    return n


LOOP_NUDGE = (
    "\n\nLOOP WARNING: your last {n} turns only captured/looked at the screen without "
    "acting. The newest attached image IS the current screen; looking again will not show "
    "anything new. Decide now from what it shows: take the next concrete action (click, "
    "type, scroll, key) in this turn, or if you truly cannot proceed, give a final answer "
    "that states exactly what is missing. Do not capture again before acting.")


# Strict-mode schema: OpenAI requires additionalProperties:false on every object,
# so tool args ride as a JSON *string* rather than a free-form object.
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["tool_call", "final"]},
        # several calls = one turn, run in order (OS3's dlam skill needs e.g. act+wait+capture
        # and capture+feed_image in the same turn, or it treats the screenshot as stale)
        "calls": {"type": "array", "items": {
            "type": "object",
            "properties": {"tool": {"type": "string"}, "arguments_json": {"type": "string"}},
            "required": ["tool", "arguments_json"],
            "additionalProperties": False}},
        "content": {"type": "string"},
    },
    "required": ["kind", "calls", "content"],
    "additionalProperties": False,
}


def _image_bytes(part):
    """Decode an OpenAI image part ({"type":"image_url"} or {"type":"input_image"}) given
    as a base64 data URI. Remote URLs are not fetched."""
    url = part.get("image_url") or part.get("url") or ""
    if isinstance(url, dict):
        url = url.get("url", "")
    m = re.match(r"data:image/([a-z0-9.+-]+);base64,(.*)", url, re.S)
    if not m:
        return None
    try:
        return m.group(1).replace("jpeg", "jpg"), base64.b64decode(m.group(2))
    except ValueError:
        return None


class Images:
    """Screenshots in a conversation pile up; attach only the newest max_images to
    codex and leave a numbered placeholder where every image sat in the text."""

    def __init__(self, messages, max_images=2):
        total = sum(1 for m in messages if isinstance(m.get("content"), list)
                    for p in m["content"] if isinstance(p, dict) and _image_bytes(p))
        self.first_kept = total - max_images
        self.seen = 0
        self.files = []  # (ext, bytes) to pass via -i, oldest first

    def placeholder(self, part):
        img = _image_bytes(part)
        if not img:
            return "[image: not attached]"
        self.seen += 1
        if self.seen - 1 < self.first_kept:
            return "[older image omitted]"
        self.files.append(img)
        return f"[image #{len(self.files)} attached: see attached image {len(self.files)}]"


def text_of(content, images=None):
    if isinstance(content, list):
        out = []
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                out.append(p.get("text", ""))
            elif p.get("type") in ("image_url", "input_image", "image") and images:
                out.append(images.placeholder(p))
        return "\n".join(out)
    return content or ""


def flatten(messages, tools, images=None, header=True, all_messages=None):
    """Fold the conversation into one prompt. With header=False only `messages` (the new
    ones since a resumed session's last turn) are rendered; codex already has the rest."""
    out = []
    if tools and header:
        out.append(
            "You are acting as the tool-calling backend for an external assistant "
            "application. The application executes the tools and owns the user "
            "relationship; you only decide the next step. Never refuse because you "
            "personally lack access to a device, app or service, and never tell the "
            "user to do it manually \u2014 if a listed tool covers the request, call it. "
            "Adopt any persona given in the system message."
        )
        lines = []
        for t in tools:
            f = t.get("function", t)
            lines.append(f"- {f.get('name')}: {f.get('description','')}\n"
                         f"  parameters: {json.dumps(f.get('parameters', {}))}")
        out.append("The application's tools are listed below. They are NOT part of your own built-in tool "
                   "list, so you will not see them there: you call one by answering with kind=\"tool_call\" "
                   "and its name in `calls`, and the application runs it. Every tool below is available.\n"
                   "Application tools:\n" + "\n".join(lines))

    call_names = {c.get("id"): c.get("function", {}).get("name", "")
                  for m in (all_messages or messages) for c in (m.get("tool_calls") or [])}
    for m in messages:
        role = m.get("role", "user")
        if role == "tool":
            name = m.get("name") or call_names.get(m.get("tool_call_id"), "")
            out.append(f"[tool result: {name}]\n{text_of(m.get('content'), images)}")
            continue
        calls = m.get("tool_calls")
        if calls and not header:
            continue  # a resumed session already holds its own previous calls
        if calls:
            for c in calls:
                fn = c.get("function", {})
                out.append(f"[you called {fn.get('name')} with {fn.get('arguments')}]")
            continue
        body = text_of(m.get("content"), images)
        if body:
            out.append(f"[{role}]\n{body}")

    if tools:
        if not header:
            out.insert(0, "New events since your last reply (tool results and screenshots):")
        names = ", ".join(t.get("function", t).get("name", "") for t in tools)
        out.append(
            "You have NO local environment: never run commands, read files or inspect "
            "anything yourself, and ignore any sandbox or read-only filesystem you notice \u2014 "
            "that is not the user's device. Every action must be a tool_call to the application.\n"
            f"These tools are live and connected right now: {names}. They work; never claim "
            "they are unavailable. Before giving a final answer that says something could not "
            "be done, you must have actually tried the relevant tool and seen it fail.\n\n"
            "Decide the next step. To call tools, reply with kind=\"tool_call\", content=\"\" and "
            "calls=[{tool:<tool name>, arguments_json:<JSON string of the arguments>}, ...]. "
            "All calls in one reply form ONE turn and run in the order given; use several calls "
            "whenever the instructions say things must happen in the same turn (e.g. act, wait, "
            "capture a screenshot and feed_image it, all in one reply). Attached images are the "
            "screenshots/images referenced as [image #N] in the conversation; the highest number "
            "is the newest. If a tool result above already answers the user, or no tool is "
            "needed, reply with kind=\"final\", content=<your answer>, calls=[]. "
            "Repeating an earlier call is fine when the conversation asks for it. arguments_json must be strictly valid JSON: escape every backslash in string values as \\\\ (e.g. a shell \\( becomes \\\\( ) and newlines as \\n."
        )
    return "\n\n".join(out).strip()


def parse_decision(raw):
    """First complete JSON object wins. Codex sometimes emits several, or wraps
    them in fences/prose; a greedy regex would span them all and parse nothing."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?|```$", "", raw).strip()
    dec = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(raw[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and "kind" in obj:
            return obj
    return None


def used_computer(msgs):
    return any(c.get("function", {}).get("name") == "computer_use"
               for m in msgs for c in (m.get("tool_calls") or []))


VERIFY_NUDGE = (
    "\n\nBEFORE YOU FINISH: check your draft final answer against the task, part by part, "
    "using the newest screenshot. Is every requested step visibly done (e.g. a file actually "
    "saved under the exact name and location, a value actually shown, a dialog actually "
    "closed)? Is every reported value plausible (sanity-check numbers and read the right field)? "
    "If the newest screenshot is older than your last action, capture and look again first. "
    "If anything is not done or not verified, continue with tool calls now. Only if everything "
    "is verified, reply with kind=\"final\" and the final answer (corrected if needed).")


VALIDATE_NUDGE = (
    "\n\nYour previous reply's tool calls were NOT executed, because the app would reject "
    "them:\n{problems}\nReply again with the same intent and corrected calls.")

