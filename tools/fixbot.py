#!/usr/bin/env python3
"""Maintainer tool: ask for approval of a fix in Discord, and read the answer.

The bot posts a card (issue, pull request, summary) with ✅ / ❌ reactions; only the bot
application's owner (or OS3_FIX_APPROVERS, comma-separated Discord user ids) counts. Reactions
instead of buttons: buttons need a bot that is always online; reactions work with plain REST.
Same token and channel as reports_to_issues.py.

  fixbot.py ask --issue 12 --pr <url> --title "..." --summary "..."   post a card
  fixbot.py check                     prints YES/NO/WAITING <pr> <issue> for every open card
  fixbot.py say "text"                post a message (e.g. "released v0.3.2")
"""
import argparse, json, os, subprocess, sys, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reports_to_issues import CHANNEL, DIR, token  # noqa: E402

PENDING = os.path.join(DIR, "pending_fixes.json")
# files where a bad change hurts every install (updates, installers, CI, auth, reports): flagged on the card
SENSITIVE = ("install.sh", "install.ps1", ".github/", "codex_os3/updater.py", "codex_os3/report.py",
             "codex_os3/server.py", "codex_os3/config.py", "codex_os3/selffix.py", "report-intake/", "tools/", "app/")


def pr_files(pr):
    """[(path, additions, deletions)] via gh; [] if gh can't tell."""
    try:
        out = subprocess.run(["gh", "pr", "view", pr, "--json", "files"], capture_output=True, text=True, timeout=60)
        return [(f["path"], f.get("additions", 0), f.get("deletions", 0)) for f in json.loads(out.stdout)["files"]]
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return []


YES, NO = "✅", "❌"


def api(method, path, body=None):
    req = urllib.request.Request("https://discord.com/api/v10" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bot " + token(), "User-Agent": "os3-router-fixbot (1.0)",
                                          "Content-Type": "application/json"})
    raw = urllib.request.urlopen(req, timeout=30).read()
    return json.loads(raw) if raw else None


def approvers():
    env = os.environ.get("OS3_FIX_APPROVERS")
    if env:
        return {x.strip() for x in env.split(",") if x.strip()}
    app = api("GET", "/oauth2/applications/@me")
    team = app.get("team") or {}
    return {m["user"]["id"] for m in team.get("members", [])} or {app["owner"]["id"]}


def load():
    try:
        return json.load(open(PENDING))
    except (OSError, ValueError):
        return {}


def save(p):
    os.makedirs(DIR, exist_ok=True)
    json.dump(p, open(PENDING, "w"), indent=1)


def card(a, state=None):
    files = a.get("files") or []
    risky = [f for f, _, _ in files if f.startswith(SENSITIVE) or f in SENSITIVE]
    colors = {None: 0xe8a33d if risky else 0x2a78d6, "yes": 0x0ca30c, "no": 0xd03b3b}
    foot = {None: f"React {YES} to merge and release · {NO} to reject",
            "yes": f"{YES} Approved: merging and releasing", "no": f"{NO} Rejected"}[state]
    return {"embeds": [{
        "title": f"Fix ready for #{a['issue']}: {a['title']}"[:250], "url": a["pr"], "color": colors[state],
        "description": a["summary"][:3500],
        "fields": [{"name": "Issue", "value": f"[#{a['issue']}](https://github.com/Nesbesss/os3-router/issues/{a['issue']})", "inline": True},
                   {"name": "Pull request", "value": f"[open]({a['pr']})", "inline": True}]
                  + ([{"name": f"Changes · +{sum(x[1] for x in files)} −{sum(x[2] for x in files)} in {len(files)} file(s)",
                       "value": "\n".join(f"`{f}` +{ad} −{de}" for f, ad, de in files[:12])[:1000]}] if files else [])
                  + ([{"name": "⚠️ Touches sensitive files: review the diff before approving",
                       "value": ", ".join(f"`{f}`" for f in risky)[:1000]}] if risky else []),
        "footer": {"text": foot}}], "allowed_mentions": {"parse": []}}


def ask(a):
    a["files"] = pr_files(a["pr"])
    m = api("POST", f"/channels/{CHANNEL}/messages", card(a))
    for e in (YES, NO):
        api("PUT", f"/channels/{CHANNEL}/messages/{m['id']}/reactions/{urllib.parse.quote(e)}/@me")
    p = load()
    p[m["id"]] = {k: a[k] for k in ("issue", "pr", "title", "summary", "files")}
    save(p)
    print(f"ASKED {a['pr']} (message {m['id']})")


def check():
    p, ok = load(), approvers()
    if not p:
        print("no open cards")
    for mid, a in list(p.items()):
        voted = {e: {u["id"] for u in api("GET", f"/channels/{CHANNEL}/messages/{mid}/reactions/{urllib.parse.quote(e)}")}
                 for e in (YES, NO)}
        state = "yes" if voted[YES] & ok else "no" if voted[NO] & ok else None
        if not state:
            print(f"WAITING {a['pr']} #{a['issue']}")
            continue
        api("PATCH", f"/channels/{CHANNEL}/messages/{mid}", card(a, state))
        del p[mid]
        print(f"{state.upper()} {a['pr']} #{a['issue']}")
    save(p)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ask")
    for f in ("--issue", "--pr", "--title", "--summary"):
        s.add_argument(f, required=True)
    sub.add_parser("check")
    sub.add_parser("say").add_argument("text")
    a = ap.parse_args()
    if a.cmd == "ask":
        ask(vars(a))
    elif a.cmd == "check":
        check()
    else:
        api("POST", f"/channels/{CHANNEL}/messages", {"content": a.text[:1900], "allowed_mentions": {"parse": []}})
        print("posted")


if __name__ == "__main__":
    main()
