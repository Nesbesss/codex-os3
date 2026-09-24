# Computer-use bench

Runs real computer-use tasks on this Mac **through the router**, playing OS3's part: it sends the
request, executes the returned `computer_use` / `feed_image` calls with OS3's own dlam scripts
(`~/.os3/computer-use/*.py`), and feeds the results and screenshots back.

**It really moves your mouse and types.** Don't use the Mac while it runs, and don't run OS3
computer-use tasks on it at the same time. It is expensive in Codex quota (one round of 6 tasks is
~15-25 M input tokens).

## Setup
1. `bench/template.json` holds OS3's tool definitions and dlam skill text. That is rabbit's text,
   so it is not in this repo. To create it, enable **captures** in the dashboard settings, run one
   computer-use task in OS3, then:
   `python3 bench/make_template.py ~/.codex-os3/captures/<task>/<file>.json`
2. Run it from **Terminal.app** (the only process that has Screen Recording + Accessibility):
   `python3 bench/bench.py reveal,textedit,calculator 2 gpt-6-luna`

Results go to `bench/results.jsonl`, transcripts to `bench/transcripts/`, and screenshots to `bench/shots/`.
