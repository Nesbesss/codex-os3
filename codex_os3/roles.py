"""Which OS3 role a request comes from, and which model/effort serves that role.

OS3 in "local" mode sends the same model id for everything, but the requests differ:
  chat        the orchestrator you talk to: create_task, notify_before_act, steer_task, …
  worker      background workers doing the job: "You are a worker agent", shell, computer_use, files
  background  small housekeeping calls: memory/fact extraction, reply review, titles (few or no tools)
"""
import json, os

ROLES = ("chat", "worker", "background")
CHAT_TOOLS = {"create_task", "notify_before_act", "steer_task", "cancel_task", "report_task_on", "render_ui"}
WORKER_TOOLS = {"computer_use", "computer_use_prepare", "shell", "file_read", "file_write", "file_edit",
                "dummy_system", "feed_image"}


def classify(body):
    tools = {t.get("function", t).get("name", "") for t in body.get("tools") or []}
    system = ""
    for m in body.get("messages") or []:
        if m.get("role") == "system":
            c = m.get("content")
            system = c if isinstance(c, str) else " ".join(p.get("text", "") for p in c or [] if isinstance(p, dict))
            break
    if "worker agent" in system.lower() or (tools & WORKER_TOOLS and not tools & CHAT_TOOLS):
        return "worker"
    if tools & CHAT_TOOLS:
        return "chat"
    if tools & BACKGROUND_MARKERS:
        return "background"
    return "chat" if tools else "background"  # unknown tools (e.g. OS3's connection test) = main chat


# tools only OS3's housekeeping calls get: memory/fact extraction and the reply reviewer
BACKGROUND_MARKERS = {"emit_facts", "emit_merged_soul", "extract_file_signals", "report_missed_action",
                      "report_correction"}


def pick(cfg, role, requested):
    """-> model string for codex_runner ("<slug>-<effort>"). With routing off, the model OS3 asked for."""
    if not cfg.get("role_routing", True):
        return requested or cfg["model"]
    r = (cfg.get("roles") or {}).get(role) or {}
    model = r.get("model") or requested or cfg["model"]
    effort = r.get("effort") or cfg["effort"]
    return f"{model}-{effort}"


# -- the models this Codex account offers -------------------------------------

FALLBACK = [
    {"slug": "gpt-6-luna", "name": "GPT-6-Luna", "description": "Fast and affordable model for easier tasks.",
     "efforts": ["low", "medium", "high", "xhigh", "max"], "default_effort": "medium"},
    {"slug": "gpt-6-sol", "name": "GPT-6-Sol", "description": "Workhorse model for coding and everyday work.",
     "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"], "default_effort": "medium"},
]


def available_models():
    """From Codex's own model cache (refreshed by every codex run), so new models show up by themselves."""
    path = os.path.join(os.path.expanduser(os.environ.get("CODEX_HOME", "~/.codex")), "models_cache.json")
    try:
        with open(path) as f:
            ms = json.load(f).get("models") or []
    except (OSError, ValueError):
        return FALLBACK
    out = [{"slug": m["slug"], "name": m.get("display_name") or m["slug"], "description": m.get("description") or "",
            "efforts": [e["effort"] for e in m.get("supported_reasoning_levels") or []] or ["medium"],
            "default_effort": m.get("default_reasoning_level") or "medium"}
           for m in ms if m.get("slug") and m.get("visibility", "list") == "list"]
    return out or FALLBACK
