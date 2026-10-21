# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Personal AI/tech news digest for one Russian-speaking reader (Tashkent): collects ~40 feeds, Gemini selects and explains,
publishes a static page to GitHub Pages and a message to Telegram. Twice a day, plus hourly breaking alerts and a Sunday weekly summary.

## Commands

Python ≥ 3.12 (pyproject). CI installs with `pip install ".[dev]"`.

```bash
pytest -q                                        # all Python tests (no network)
pytest -q tests/test_llm.py::test_name           # single test
ruff check digest tests && mypy                  # lint + types, CI runs it before tests
node --test "worker/test/*.test.js"              # worker tests, no npm deps needed

python -m digest build [--dry-run]               # daily issue → site/*.html + out/message.json
python -m digest weekly                          # weekly summary → site/week-*.html + out/weekly.json
python -m digest alert                           # hourly breaking-news check, sends to Telegram directly
python -m digest send [--message weekly.json]    # send out/<message> to Telegram
python -m digest snapshot                        # freeze today's news into eval/snapshots/ (committed baseline)
python -m digest eval [--runs N] [snap.json]     # run current prompts on snapshots, print slop metrics

cd worker && npx wrangler deploy                 # deploy feedback/scheduler worker; secrets via `wrangler secret put`
```

Without a local 3.12 venv: `uv run --no-project --python 3.12 --with httpx --with feedparser --with pytest pytest -q`
(`--no-project` matters: otherwise uv rewrites `.venv` and creates `uv.lock`).

Local runs read `.env` (`load_env` in `digest/__main__.py`; never overrides real env). `build` needs `GEMINI_API_KEY`, `SITE_URL`;
`FEEDBACK_URL`/`FEEDBACK_TOKEN` optional. `--dry-run` only skips saving `state/` — it still overwrites `site/index.html`,
`site/archive.html` and the issue page. Covers need Chrome (`CHROME_PATH` locally); missing Chrome just skips the cover.

## Gemini budget (free tier — stay within it)

- The key is free tier (AI Studio project `ai-daig`), no billing — don't propose a paid key. `gemini-flash-latest`
  (now 3.8 Flash): 5 RPM, 250K TPM, **20 requests/day**, reset at midnight Pacific (≈12–13 Tashkent). Lite models
  (`gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`): 15 RPM, 500/day. Quotas are per model.
- Model chains (`__main__.model_chains`, sources.toml): select and explain go `model` (3.8 Flash) → `extras_model`
  (3.6 Flash, own 20/day) → `lite_model`; `extras` («Ещё за день» + tricks) goes `extras_model` → `lite_model`;
  `alert` triages on `lite_model` → `extras_model` and re-checks on `extras_model` → `model` only what Lite marked
  urgent (3.8 quota is kept for builds). `build` = 2 calls on 3.8 + 1 on 3.6, `weekly` = 1 → ≈5 calls/day on 3.8.
- **503 counts against RPD** (verified 2026-10-21: AI Studio counters matched exactly, 3.6 = 6, 3.8 = 17, incl.
  every 503). Free-tier 3.8/3.6 shed load with an instant 503 «high demand» (1–2 s) that clears within minutes.
- `llm.generate` makes ONE attempt; `llm.generate_first` moves to the next model on any failure (429, 5xx, timeout,
  404 of a retired model). The only same-model retry: `select` waits `llm.OVERLOAD_WAIT` (60 s) once after a 503.
  Don't add more retries: every retry, timeout and 503 costs a request. Logs show latency and thinking tokens.
  Only `select` failing on every model aborts the build; a failing `extras` or `explain` only logs.
- Free-tier queue to 3.8 Flash reached 80 s before any work (2026-10-21), select needs another 1–2 min (≈44K tokens):
  timeout is `llm.TIMEOUT` = 360 s.
- `eval` spends the same daily quota (2 calls on 3.8 + 1 on 3.6 per run): 1–2 runs a day, never right before the 08:07 / 18:07 builds.
- `antigravity-*` models accept only the Interactions API (no JSON mode) — unusable with `llm.generate`.

## Architecture

Three parts, wired together across repos/services:

1. **`digest/` (Python)** — the pipeline, run by GitHub Actions.
2. **`worker/` (Cloudflare Worker `ai-digest-feedback`)** — the *scheduler* and the *feedback store*.
3. **`state/` + `site/` in git** — persistent memory and the published archive; the CI bot (`digest-bot`) commits them.

### Scheduling flow

GitHub cron is not used (it was unreliable). Cloudflare cron in `worker/wrangler.jsonc` → `scheduled()` in
`worker/src/index.js` → `workflow_dispatch` of `digest.yml` (morning/evening slot, `weekly=true` on Sunday morning
Tashkent) or `alert.yml`. Keys of `JOBS` in `index.js` must be **exactly** the cron strings in `wrangler.jsonc`.
All human-facing time is Tashkent, UTC+5 (`DISPLAY_TZ` in `render.py`); evening slot starts at 13:00 (`memory.EVENING_FROM_HOUR`).

### `build` pipeline (`digest/__main__.py:build`)

`fetch_all` (sources.toml) → `SeenStore.take_new` (cross-run dedup, `state/seen.json`) → `dedupe` (within run) →
`fetch_feedback` (votes/read marks from worker) → `llm.select` (Gemini picks 8–12 events + voices) → `llm.extras`
(second call over the records `select` left in noise, original numbering kept: 10–15 «Ещё за день» one-liners in
`Digest.more` + 2–4 tricks in section `tips`, rest is noise; `llm.CORPORATE_MONEY` drops funding/revenue lines
the model let through) → `resolve_gnews` (events and one-liners) →
`attach_discussions` (HN) → `fetch_articles` → `llm.explain` (detailed breakdowns) → `record_issue` (ids → stories →
archive) → cover → `render_page` → `out/message.json` → save state.

- **Fail-soft is the design rule.** A failing source, HN lookup, Google News decoding, article fetch, `extras`, `explain`,
  cover or feedback fetch logs `log.error` and the issue still ships. Only `select` (the LLM selection) failing aborts the build.
  `alert` raises only if *every* alert source failed (silent exit would hide a dead feature).
- **State is committed last.** In CI: build → deploy Pages → send Telegram → commit `state/` + `site/`. A failed run
  leaves news unseen so the rerun picks them up. `alert.yml` commits only `state/alerts.json`, in its own concurrency
  group; both push with `pull --rebase` retries. Expect many `chore(digest): …` bot commits on `origin/main`.
- **Source order in `sources.toml` is dedup priority** (first seen stays primary), except aggregators
  (`news.google.com`, `techmeme.com`) always yield to a direct link. `alert = true` marks sources checked hourly.

### Cross-file contracts

- **Entry id** `YYYYMMDD-N`: numbered across both issues of a day, never reused (`Archive.next_index`). The worker
  validates it with `ID_RE = /^\d{8}-\d{1,3}$/`; Telegram buttons use `callback_data` `u:<id>` / `d:<id>`. Changing the
  format means changing the worker too.
- **Sections**: `llm.SECTIONS` (also the Gemini schema enum) ↔ `render.SECTION_LABELS` / `ROW_SECTIONS` / `BRIEF_LABELS`;
  old archived names are mapped in `weekly.LEGACY_SECTIONS`. `llm.EXTRA_SECTIONS` (`voices`, `tips`) are not news:
  they rank after events, never become the hero, and render after the «Ещё за день» block.
- **«Ещё за день»** (`Digest.more`, `llm.Mention`) has no entry id: not in the archive, votes, stories or the weekly.
- **Templates** are `string.Template` (`digest/templates/page.html`, `cover.html`): a literal `$` must be `$$`.
  `cover.py` regex-extracts the hero landscape SVG from `page.html` — changing that CSS changes the cover.
- **Published pages are static.** `site/*.html` of past issues are not regenerated by template changes; only new builds
  pick them up. `site/og/` (covers) is gitignored on purpose.
- **Prompts** (Russian) live next to their schemas: `llm.PROMPT`/`EXPLAIN_PROMPT` (+ shared `VOICE`), `alert.ALERT_PROMPT`,
  `weekly.WEEK_PROMPT`. All Gemini calls go through `llm.generate_first` → `llm.generate` (JSON `responseSchema`, one attempt per model).

### Security boundaries (keep them)

- Copyable terminal commands are shown only if they appear **verbatim** in the source text **and** the item is from a
  trusted category (`lab`, `release`, `engineer`, `newsletter`) on the feed's own host (`llm._commands_trusted`).
  The same rule guards a trick's `Entry.copy` (command or prompt): a prompt pasted into an agent runs like a command.
- The Telegram bot token is in request URLs and Actions logs are public: `httpx`/`httpcore` logging is raised to WARNING
  and `TelegramError` drops the original exception. Don't log request URLs.
- Worker: constant-time token compare, page endpoints accept only `PAGE_ORIGIN`, Telegram votes only from `OWNER_CHAT_ID`;
  KV records are duplicated into key metadata so `/votes` stays within the KV operation limit.

## Git workflow

- Commit and push right after a finished, verified change — don't ask first. Work directly on `main`; no feature
  branch for routine changes.
- `git pull --rebase` before every push: the CI bot pushes `state/`/`site/` commits to `origin/main` every hour.

## Conventions

- Comments, docstrings and log messages are in Russian; comments explain *why*, often citing the incident that caused the code.
- Tests are pure-function, inline fixtures, no network; the worker tests use an in-memory KV mock (`memoryKV`).
- Commits: `feat|fix|chore(<scope>): …` in English, scopes seen: `digest`, `site`, `page`, `worker`, `alert`, `ci`, `sources`.
