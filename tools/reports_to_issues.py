#!/usr/bin/env python3
"""Maintainer tool: turn os3-router problem reports from the Discord channel into GitHub issues.

Runs on the maintainer's machine (e.g. from an OS3 scheduled task), never on users' machines:
  - reads new messages with a Discord *bot* (webhooks can't read). Token in $DISCORD_BOT_TOKEN
    or ~/.os3-reports/discord_token (chmod 600); the bot needs "Message Content Intent" and
    read access to the reports channel
  - problems (🔴 errors, 🟠 warnings, 📝 reports from "Report a problem") become issues with the
    label `report` via your `gh` login; the same problem again becomes a "+1" comment
  - prints one line per report (NEW / DUP / skip) for whoever runs it

  python3 tools/reports_to_issues.py            # process new messages
  python3 tools/reports_to_issues.py --dry-run  # show what it would do
"""
import hashlib, json, os, re, subprocess, sys, urllib.request

REPO = os.environ.get("OS3_REPORTS_REPO", "Nesbesss/os3-router")
CHANNEL = os.environ.get("OS3_REPORTS_CHANNEL", "1553142408273731688")
DIR = os.path.expanduser("~/.os3-reports")
STATE = os.path.join(DIR, "last_message_id")
DRY = "--dry-run" in sys.argv


def token():
    t = os.environ.get("DISCORD_BOT_TOKEN")
    if not t:
        try:
            t = open(os.path.join(DIR, "discord_token")).read().strip()
        except OSError:
            sys.exit(f"no bot token: set DISCORD_BOT_TOKEN or put it in {DIR}/discord_token")
    return t


def discord(path):
    req = urllib.request.Request("https://discord.com/api/v10" + path,
                                 headers={"Authorization": "Bot " + token(), "User-Agent": "os3-router-reports (1.0)"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def gh(*args, stdin=None):
    r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"gh {' '.join(args[:2])} failed: {r.stderr.strip()}")
    return r.stdout


def fingerprint(kind, text):
    """Same problem from different installs/times -> same id: drop numbers, ids and paths."""
    first = next((line for line in text.splitlines()[1:] if line.strip()), "")
    norm = re.sub(r"[0-9a-f]{6,}|\d+|/\S+", "#", first.lower())[:160]
    return hashlib.sha1(f"{kind}|{norm}".encode()).hexdigest()[:12]


def main():
    try:
        last = open(STATE).read().strip()
    except OSError:
        last = "0"
    msgs = sorted(discord(f"/channels/{CHANNEL}/messages?limit=100" + (f"&after={last}" if last != "0" else "")),
                  key=lambda m: int(m["id"]))
    if not msgs:
        print("nothing new")
        return
    if not DRY:
        gh("label", "create", "report", "--repo", REPO, "--color", "d93f0b",
           "--description", "Problem reported by an os3-router install", "--force")
    open_issues = json.loads(gh("issue", "list", "--repo", REPO, "--label", "report", "--state", "open",
                                "--limit", "200", "--json", "number,body")) if not DRY else []
    for m in msgs:
        text = m.get("content") or ""
        head = text.split("\n", 1)[0]
        kind = (re.search(r"\*\*(\w+)\*\*", head) or [None, "report"])[1]
        if not m.get("webhook_id") or not head[:2].strip() or head.startswith("🟢"):
            print(f"skip {m['id']}: {head[:80]}")  # not from the router, or only "fixed / passed"
            continue
        fp = fingerprint(kind, text)
        dup = next((i for i in open_issues if f"fp:{fp}" in (i.get("body") or "")), None)
        if dup:
            print(f"DUP #{dup['number']} {kind}: {head[:80]}")
            if not DRY:
                gh("issue", "comment", str(dup["number"]), "--repo", REPO, "--body-file", "-",
                   stdin=f"+1, again reported:\n\n{text}")
        else:
            body_line = next((line for line in text.splitlines()[1:] if line.strip()), kind)
            title = f"[report] {kind}: {re.sub(r'^[>* -]+', '', body_line)[:80]}"
            print(f"NEW {kind}: {title}")
            if not DRY:
                url = gh("issue", "create", "--repo", REPO, "--label", "report", "--title", title, "--body-file", "-",
                         stdin=f"{text}\n\n<sub>from the reports channel · fp:{fp}</sub>").strip()
                open_issues.append({"number": url.rsplit("/", 1)[-1], "body": f"fp:{fp}"})
                print(f"    {url}")
    if not DRY:
        os.makedirs(DIR, exist_ok=True)
        open(STATE, "w").write(msgs[-1]["id"])


if __name__ == "__main__":
    main()
