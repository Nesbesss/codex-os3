# Changelog

## 0.2.0 (unreleased)
- Renamed to **os3-router** (was codex-os3); internal names, paths and services are unchanged, so upgrades
  need nothing
- **Claude Code backend:** any role can use a Claude model (`claude-sonnet-5`, `claude-opus-5-5`, …) through
  the official `claude` CLI and your own Claude Code login; mix it with Codex per role
- Dashboard shows the 5-hour and weekly limits per subscription; `doctor` checks Claude Code when a role uses it
- Token card follows the chart's range (it was empty right after midnight)

## 0.1.1 (2026-09-24)
- Per-role models: **Small** (main chat), **Standard** (workers), **Background** (memory/review), chosen in the
  dashboard from the models your Codex account offers; token use per role
- Only passes `--disable` flags the installed Codex knows; the installer updates a too-old Codex CLI
- Fixed OS3's connection test failing now and then (the model thought OS3's tools were unavailable)
- Watchdog runs inside the worker, so upgrades update it
- Windows installer fixes found by CI (Python detection, console encoding, tray app port)
- App icon

## 0.1.0 (2026-09-24)
First public version, grown from a live prototype used with rabbit OS3.

- OpenAI-compatible router over `codex exec`: chat, streaming, tool calling with several calls per turn
- Computer use: screenshots passed as images (newest 2), one persistent Codex session per OS3 task
- Tool-call repair and validation (JSON escapes, node ids, dlam scripts, OS3 schemas, `act.py` arguments)
- Self-checks: false "unavailable" answers, unverified "done" after computer use, screenshot loops
- Hang detection (90 s without activity), readable usage-limit replies with the reset time
- Supervisor with zero-downtime reloads; the worker exits if the supervisor is killed
- Watchdog: detects a silently dead rabbit-agent LLM tunnel and restarts the agent through its own
  scheduler; optional TypeSafe Jev second opinion; webhook alerts
- Local dashboard: limits, tokens, requests, setup with copy buttons, watchdog events, per-task log export
  (redacted), settings; remote access via `/login?key=`
- Installers: macOS (launchd), Linux (systemd user service or cron), Windows (Task Scheduler, beta)
- macOS menu bar app, Windows tray icon (beta)
