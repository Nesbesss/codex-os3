# Changelog

## 0.1.0 (unreleased)
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
