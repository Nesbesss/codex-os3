# codex-os3

Use your **Codex / ChatGPT subscription** as the LLM for **rabbit OS3**: chat, tool calling,
workers and computer use. It includes a local dashboard, token and limit tracking, a watchdog that
repairs a stuck rabbit-agent, and a menu bar app on macOS.

```
OS3 cloud ──▶ rabbit-agent (your machine) ──▶ codex-os3 router (localhost:11435) ──▶ codex exec ──▶ your subscription
```

> ⚠️ **Read first.** This drives the official Codex CLI with your own login. Check whether OpenAI's
> terms allow using your subscription this way for a third-party app. That decision is yours. Heavy
> agent use also burns through your 5-hour and weekly limits quickly (the dashboard shows both).

## Install

On the machine that runs your OS3 node (rabbit-agent), in a terminal **on that machine** (not over SSH):

**macOS / Linux**
```sh
curl -fsSL https://raw.githubusercontent.com/Nesbesss/codex-os3/main/install.sh | bash
```

**Windows (beta: no real OS3 test yet)**
```powershell
irm https://raw.githubusercontent.com/Nesbesss/codex-os3/main/install.ps1 | iex
```

The installer:
1. checks Python 3.9+, installs the Codex CLI if needed, and runs `codex login`
2. checks that the rabbit-agent (OS3 node) is on this machine
3. installs the router as a service (launchd / systemd / Task Scheduler) that starts at login and restarts on crashes
4. installs the menu bar app (macOS) or tray icon (Windows)
5. opens the setup page and **waits until OS3 connects**

Then, in OS3 go to **Settings → API keys**, provider **local**, and enter:

| field | value |
|---|---|
| device | **this machine** (the one you installed on) |
| endpoint | `http://localhost:11435/v1` |
| model id | `gpt-6-luna` (see [Models](#models)) |
| api key | shown by the installer and on the setup page |
| context window (advanced) | `200000` |

**Several nodes?** Install the router on **one** machine, ideally the one that is always on, and
pick it as the LLM device. Tasks still run on every node. No Tailscale, ngrok, or open ports are needed,
because rabbit relays model calls through the rabbit-agent of the device you picked.

## What it does for OS3

Codex is an agent CLI, not a chat API, so the router does a lot of translation:

- **Tool calling** via a strict output schema, with **several calls per turn** (OS3's computer-use
  skill requires "act → wait → screenshot → look" in one turn)
- **Screenshots** go to the model as real images; the newest 2 are attached
- **One Codex session per task** (`codex exec resume`): each turn sends only new events instead of re-reading
  the whole conversation, and the model keeps its own reasoning
- **Call repair and validation** before OS3 sees a call: broken JSON escapes, device names or garbled device ids
  instead of ids, dlam actions sent as script names, missing `feed_image` after a screenshot, and every
  argument checked against OS3's tool schemas and `act.py`'s own parser. Invalid calls go back to the model once
  to be fixed.
- **Self-checks:** a false "tool not available" gets one retry, "done" after computer use gets one
  verify pass, and a screenshot loop gets a nudge. These extra steps can never make a request fail.
- **Hang handling:** no Codex activity for 90 s means the call is killed and retried once
- **Usage limit** shows up in OS3 as a clear message with the reset time, not "something went wrong"
- **Zero-downtime updates:** upgrades swap the worker process while running requests finish

## Dashboard, menu bar app, watchdog

`http://localhost:11435/` (local only; from elsewhere it needs the API key):

- **Dashboard:** 5-hour and weekly limits with reset countdowns, tokens per hour, recent requests
- **Setup:** OS3 values with copy buttons, key rotation, checks, and a "connected" indicator
- **Watchdog:** events and actions, plus a manual rabbit-agent restart
- **Tasks & export:** one row per OS3 task and a **log export** (zip with a report, the timeline and
  router fixes, with secrets redacted), meant for bug reports
- **Settings:** model, effort, watchdog, captures, alert webhook, optional Jev key

**Watchdog.** The rabbit-agent's LLM tunnel can die silently: it still says "connected" and still runs
commands, but OS3's model requests never arrive, and every task fails with *"Local LLM device can't be
reached"*. The watchdog detects this from hard evidence (our reply asked OS3 to run tools, the agent ran them
or aborted the task, and no follow-up request came) and restarts the agent **through its own scheduler**,
so on macOS it keeps its Accessibility and Screen Recording permissions. It acts at most once per stalled reply
and ignores normal waits (`ask_user`, `wait`, workers). It also warns about usage limits and unstable Codex
connections, and can post alerts to a webhook (e.g. `https://ntfy.sh/<topic>`).

**Jev (optional).** With a [TypeSafe](https://typesafe.ai) key, the watchdog also asks Jev for a second
opinion on the ambiguous case ("is this silence normal?"). Jev receives only timings, tool names and agent log
lines, never your messages or screenshots. Rules stay in charge: Jev can only escalate when it is ≥ 90% sure
*and* the hard signals agree.

## Models

| model id | notes |
|---|---|
| `gpt-6-luna` | default: clean tool calls, rarely asks unnecessary questions. **Can misread digits in screenshots** (e.g. 226295 → 26295), so double-check exact numbers |
| `gpt-5.6-luna` | reads screens more precisely; makes more invalid calls (fixed by the router) and asks for permission more often |
| `…-high` | more reasoning: slower, and uses more of your limit |

## Privacy and security

- The router listens on `127.0.0.1` only by default, and `/v1` always needs the API key
- It stores **metadata** (timings, token counts, tool names), not message contents. "Captures"
  (full requests, including screenshots and anything you typed) are **off** by default.
- History is kept for 7 days (configurable)
- Exports redact keys, tokens and password-like strings
- It never starts services from SSH on macOS (processes started that way lose their permissions)

## Commands

```sh
cd ~/.codex-os3/app && python3 -m codex_os3 <command>
  status | doctor | setup-info | key [--rotate] | reload | export <task>
```
Uninstall: `bash install.sh --uninstall [--purge]` · Windows: `install.ps1 -Uninstall [-Purge]`

## Development

```sh
python3 -m unittest discover -s tests                     # offline tests (no Codex calls)
CODEX_OS3_HOME=/tmp/x CODEX_OS3_PORT=11499 python3 -m codex_os3 serve &
CODEX_OS3_HOME=/tmp/x CODEX_OS3_PORT=11499 python3 tests/live_smoke.py --reload   # uses real Codex quota
app/macos/build.sh                                        # menu bar app (Xcode 15+)
```
`bench/` runs real computer-use tasks on a Mac through the router (see `bench/README.md`). It is
expensive in Codex quota; never run it in CI.

## Platform status

| | install / service / upgrade / uninstall | with a real rabbit-agent + OS3 |
|---|---|---|
| **macOS** | ✓ launchd, tested on real Macs | ✓ in daily use |
| **Linux** | ✓ systemd user service and cron fallback (CI + Docker) | not yet |
| **Windows** | ✓ Task Scheduler (CI on Windows Server) | not yet (beta) |

CI runs the full test suite on macOS, Linux and Windows with Python 3.9 and 3.12, against a fake Codex
CLI (no quota), plus end-to-end installer runs on Linux and Windows.

MIT license.
