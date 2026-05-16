# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Bot

```bash
python bot.py
```

A `venv/` directory is present — activate it first if needed. No build step, test suite, or lint tooling exists. Dependencies (`discord.py`, `twikit`, `twscrape`) must be installed manually — there is no `requirements.txt`.

## Configuration

Copy `cfg_format.json` to `config.json` and fill in values before running. `config.json` is gitignored. Key fields:

- `discord_token`, `admin_user_id`, `discord_channel_id` — Discord connection
- `twitter_scraper` — `"twikit"` or `"twscrape"` (selects which scraper backend is used)
- `twitter_target_user`, `twitter_poll_count`, `twitter_poll_rate_hours`, `twitter_min_likes` — scraping behavior
- `post_interval_days`, `post_window_start/end`, `post_spacer_hours`, `post_chance_percent` — probabilistic posting schedule
- `online_window_start/end`, `online/offline_min/max_minutes` — presence simulation

All time windows are interpreted in **America/New_York** (EST/EDT).

Runtime state is persisted to `state.json` with keys: `next_post_time`, `queued_post_id`, `paused`, `last_post_time`, `list_offset`. Messages are stored in `saved_messages.json` as a list of objects with keys: `id`, `text`, `likes`, `source` (`"scraped"` or `"custom"`), `posted_to_discord`, `added_at`.

## Scraper Authentication

**twikit** (`scraper.py`) tries credentials in this order:
1. `twitter_raw_cookies_file` — Cookie-Editor browser extension export (list of `{name, value}` dicts)
2. `twitter_cookies_file` — twikit's own saved cookie format
3. Programmatic login via `twitter_username`/`twitter_email`/`twitter_password` (unreliable; cookie export is preferred)

**twscrape** (`scraper_tw.py`) stores account credentials in a SQLite database (`twscrape_db_path`, default `twscrape_accounts.db`). Each scrape call re-adds the account (silently ignores "already exists"), then calls `login_all()`. Cookie-based auth uses the `twscrape_cookies` field (`ct0=...; auth_token=...`).

## Architecture

The bot is a single Discord client instance split into functional modules — no classes, module-level globals for `config`, `state`, and `messages`.

**Three background task loops run concurrently after `on_ready`:**
1. **Presence manager** (1 min interval) — cycles the bot between online/invisible using configured time windows and random durations
2. **Post heartbeat** (7 min interval) — core posting logic; checks pause state, post day, time window, spacer hours, and a random chance roll before posting
3. **Scrape loop** (1 hr interval, internally gated by `twitter_poll_rate_hours`) — fetches tweets from the target account, adds eligible ones to `saved_messages.json`, and ensures a message is queued

**Module responsibilities:**

| File | Purpose |
|------|---------|
| `bot.py` | Discord client, event handlers, admin command dispatch, three task loops |
| `storage.py` | Atomic load/save for `config.json`, `state.json`, `saved_messages.json` using `.tmp` + `os.replace()` |
| `messages.py` | Filter unposted messages, manage like counts, add custom/scraped tweets, deduplicate |
| `scheduler.py` | Calculate next post time, determine if today is a post day, ensure a message is queued |
| `time_utils.py` | EST timezone helpers, HH:MM window parsing (supports midnight crossover), min/max parsing |
| `discord_utils.py` | Typing animation, admin notification, channel lookup, message history check |
| `scraper.py` | `twikit`-based scraper (cookie login or username/password) |
| `scraper_tw.py` | `twscrape`-based scraper (account pool approach) |

**Admin control** is via Discord DMs only. Any user can claim admin with `adminme` if unclaimed. Commands include short aliases (`c` for config, `sh` for show, `ty` for immediate post to channel, `sc` for scrape, etc.). The `get` command mirrors `set` but reads values rather than writing them.

**Scraper selection** is config-driven at runtime: `scraper.py` is used when `twitter_scraper == "twikit"`, `scraper_tw.py` when `"twscrape"`. Both return the same data shape (`id`, `text`, `likes`) so the scrape loop is backend-agnostic.

**Post eligibility** requires all of: not paused, today is a scheduled post day, current time is within `post_window_start/end`, at least `post_spacer_hours` since the last post (checked via channel history), and a random roll under `post_chance_percent`.
