import random
from datetime import datetime, timedelta

import discord
from discord.ext import tasks

import scraper
import scraper_tw
from storage import load_config, save_config, load_state, save_state, load_messages, save_messages
from time_utils import now_est, time_in_window, parse_window, parse_minmax
from messages import (get_unposted_messages, pick_random_unposted, find_message_by_id,
                      add_scraped_tweets, add_custom_message)
from scheduler import calculate_next_post_time, is_post_day, ensure_queued_post
from discord_utils import (send_with_typing, notify_admin,
                           hours_since_last_channel_message, get_post_channel)

# ---------------------------------------------------------------------------
# Module-level globals
# ---------------------------------------------------------------------------

config: dict = {}
state: dict = {}
messages: list[dict] = []


async def _scrape(cfg: dict) -> list[dict]:
    if cfg.get("twitter_scraper") == "twscrape":
        return await scraper_tw.scrape_tweets(cfg)
    return await scraper.scrape_tweets(cfg)


client = discord.Client()

# Presence cycle tracking — not persisted, recalculated each restart
_presence_online: bool = False
_presence_until: datetime | None = None

# Scrape timing — not persisted, recalculated each restart
_last_scrape_time: datetime | None = None

# ---------------------------------------------------------------------------
# Discord events
# ---------------------------------------------------------------------------

@client.event
async def on_ready() -> None:
    global config, state, messages
    print(f"[ready] Logged in as {client.user}")

    config = load_config()
    state = load_state()
    messages = load_messages()

    now = now_est()

    # Recalculate next_post_time if missing or in the past
    npt_raw = state.get("next_post_time")
    if npt_raw:
        try:
            npt = datetime.fromisoformat(npt_raw)
            if npt < now:
                state["next_post_time"] = calculate_next_post_time(config, now).isoformat()
                save_state(state)
                print("[ready] next_post_time was in the past — recalculated.")
        except Exception:
            state["next_post_time"] = calculate_next_post_time(config, now).isoformat()
            save_state(state)
    else:
        state["next_post_time"] = calculate_next_post_time(config, now).isoformat()
        save_state(state)

    if not ensure_queued_post(state, config, messages):
        print("[ready] No unposted messages available for queue.")

    presence_manager.start()
    post_heartbeat.start()
    scrape_loop.start()

    await client.change_presence(status=discord.Status.invisible)
    print("[ready] Bot is running.")

# ---------------------------------------------------------------------------
# Task loop 1: Presence manager (every 1 minute)
# ---------------------------------------------------------------------------

@tasks.loop(minutes=1)
async def presence_manager() -> None:
    global _presence_online, _presence_until
    now = now_est()
    in_window = time_in_window(
        now,
        config.get("online_window_start", "09:00"),
        config.get("online_window_end", "23:00"),
    )

    if not in_window:
        if _presence_online:
            await client.change_presence(status=discord.Status.invisible)
            _presence_online = False
            _presence_until = None
        return

    if _presence_until is None or now >= _presence_until:
        if _presence_online:
            lo = float(config.get("offline_min_minutes", 5))
            hi = float(config.get("offline_max_minutes", 20))
            duration = random.uniform(lo, hi)
            _presence_until = now + timedelta(minutes=duration)
            _presence_online = False
            await client.change_presence(status=discord.Status.invisible)
        else:
            lo = float(config.get("online_min_minutes", 10))
            hi = float(config.get("online_max_minutes", 45))
            duration = random.uniform(lo, hi)
            _presence_until = now + timedelta(minutes=duration)
            _presence_online = True
            await client.change_presence(status=discord.Status.online)


@presence_manager.error
async def presence_manager_error(error: Exception) -> None:
    print(f"[presence_manager] Error: {error}")
    await notify_admin(client, config, f"Ey boss, the presence thing had a hiccup: {error}")

# ---------------------------------------------------------------------------
# Task loop 2: Post heartbeat (every 7 minutes)
# ---------------------------------------------------------------------------

@tasks.loop(minutes=7)
async def post_heartbeat() -> None:
    if state.get("paused"):
        return
    if not is_post_day(state):
        return

    now = now_est()
    if not time_in_window(
        now,
        config.get("post_window_start", "10:00"),
        config.get("post_window_end", "20:00"),
    ):
        return

    if not ensure_queued_post(state, config, messages):
        await notify_admin(
            client, config,
            "Ey boss, I got nothin' left to post — the queue's all dried up, capisce? "
            "Try 'scrape' or 'submit' to add more."
        )
        return

    roll = random.randint(1, 100)
    if roll > int(config.get("post_chance_percent", 30)):
        return

    try:
        channel = await get_post_channel(client, config)
    except Exception as e:
        await notify_admin(client, config, f"Ey boss, I can't find the channel: {e}")
        return

    hours_elapsed = await hours_since_last_channel_message(channel)
    spacer = float(config.get("post_spacer_hours", 4.0))

    if hours_elapsed is not None and hours_elapsed < spacer:
        next_dt = calculate_next_post_time(config, now)
        state["next_post_time"] = next_dt.isoformat()
        save_state(state)
        formatted = next_dt.strftime("%A, %B %d at %I:%M %p %Z")
        await notify_admin(
            client, config,
            f"Ey boss, I wanted to post but somebody was already talking in there "
            f"{hours_elapsed:.1f} hours ago — not enough space. "
            f"I'll try again {formatted}, boss."
        )
        return

    msg = find_message_by_id(messages, state.get("queued_post_id"))
    if msg is None:
        ensure_queued_post(state, config, messages)
        return

    try:
        await send_with_typing(channel, msg["text"])
    except Exception as e:
        await notify_admin(client, config, f"Ey boss, I tried to post but somethin' went wrong: {e}")
        return

    msg["posted_to_discord"] = True
    save_messages(messages)

    state["last_post_time"] = now.isoformat()
    state["queued_post_id"] = None
    state["next_post_time"] = calculate_next_post_time(config, now).isoformat()
    save_state(state)

    ensure_queued_post(state, config, messages)
    save_state(state)


@post_heartbeat.error
async def post_heartbeat_error(error: Exception) -> None:
    print(f"[post_heartbeat] Error: {error}")
    await notify_admin(client, config, f"Ey boss, the posting machine had a hiccup: {error}")

# ---------------------------------------------------------------------------
# Task loop 3: Scrape loop (every 1 hour, internal rate gate)
# ---------------------------------------------------------------------------

@tasks.loop(hours=1)
async def scrape_loop() -> None:
    global _last_scrape_time
    now = now_est()
    poll_rate = float(config.get("twitter_poll_rate_hours", 2))

    if _last_scrape_time is not None:
        elapsed = (now - _last_scrape_time).total_seconds() / 3600
        if elapsed < poll_rate:
            return

    _last_scrape_time = now

    try:
        new_tweets = await _scrape(config)
    except Exception as e:
        await notify_admin(client, config, f"Ey boss, the Twitter machine's actin' up: {e}")
        return

    added = add_scraped_tweets(messages, new_tweets, config)
    print(f"[scrape] Got {len(new_tweets)} tweets, {added} new added.")

    ensure_queued_post(state, config, messages)
    save_state(state)


@scrape_loop.error
async def scrape_loop_error(error: Exception) -> None:
    print(f"[scrape_loop] Error: {error}")
    await notify_admin(client, config, f"Ey boss, the scraper loop blew a fuse: {error}")

# ---------------------------------------------------------------------------
# Message event & command routing
# ---------------------------------------------------------------------------

@client.event
async def on_message(message: discord.Message) -> None:
    if message.guild is not None:
        return
    if message.author.id == client.user.id:
        return

    raw = message.content.strip()
    if not raw:
        return
    raw_lower = raw.lower()

    if raw_lower == "adminme":
        await handle_adminme(message)
        return

    admin_id = config.get("admin_user_id", 0)
    if not admin_id or message.author.id != int(admin_id):
        return

    if raw_lower == "show me" or raw_lower.startswith("show me "):
        await handle_show(message, raw[7:].strip())
        return

    parts = raw.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    await dispatch_command(message, cmd, arg)


async def dispatch_command(message: discord.Message, cmd: str, arg: str) -> None:
    table = {
        "help": handle_help,       "h":  handle_help,
        "show": handle_show,       "sh": handle_show,
        "config": handle_config,   "c":  handle_config,
        "list": handle_list,       "li": handle_list,
        "more": handle_more,       "mo": handle_more,
        "scrape": handle_scrape,   "sc": handle_scrape,
        "skip": handle_skip,       "sk": handle_skip,
        "shuffle": handle_shuffle, "sf": handle_shuffle,
        "pause": handle_pause,     "pa": handle_pause,
        "nextpost": handle_nextpost, "np": handle_nextpost,
        "submit": handle_submit,   "su": handle_submit,
        "type": handle_type,       "ty": handle_type,
        "set": handle_set,
        "get": handle_get,         "ge": handle_get,
        "kill": handle_kill,       "k":  handle_kill,
    }
    handler = table.get(cmd)
    if handler:
        await handler(message, arg)

# ---------------------------------------------------------------------------
# Command: adminme
# ---------------------------------------------------------------------------

async def handle_adminme(message: discord.Message, arg: str = "") -> None:
    current_admin = config.get("admin_user_id", 0)
    sender_id = message.author.id

    if not current_admin or current_admin == 0:
        config["admin_user_id"] = sender_id
        save_config(config)
        await message.channel.send(
            "You're the boss now, boss. The whole operation's yours."
        )
        return

    if int(current_admin) == sender_id:
        await message.channel.send(
            "You're already the boss, boss. Relax."
        )
        return

    await message.channel.send(
        "Sorry, pal — we already got a boss around here. Only one at a time, capisce?"
    )

# ---------------------------------------------------------------------------
# Command: help
# ---------------------------------------------------------------------------

async def handle_help(message: discord.Message, arg: str = "") -> None:
    lines = [
        "Ey boss, here's what I know how to do:",
        "",
        "**Info**",
        "`show` / `sh`       — Show the queued next post",
        "`config` / `c`      — Show all config values",
        "`list` / `li`       — List postable tweets (5 at a time)",
        "`more [5|10]` / `mo` — Show 5 or 10 more from the list",
        "`nextpost` / `np`   — Show when I'm posting next",
        "`help` / `h`        — This right here, boss",
        "",
        "**Actions**",
        "`type [text]` / `ty` — Post something to the channel right now",
        "`submit [text]` / `su` — Add a custom message to the stash",
        "`scrape` / `sc`     — Force a Twitter scrape right now",
        "`skip` / `sk`       — Skip the queued post (marks it done)",
        "`shuffle` / `sf`    — Swap the queued post (keeps it available)",
        "`pause` / `pa`      — Toggle posting on/off",
        "",
        "**Set values** (format: `set XX value`)",
        "`set ch [id]`       — Channel ID",
        "`set ti [n]`        — Post interval (days)",
        "`set wi [HH:MM - HH:MM]` — Post time window",
        "`set sp [h]`        — Spacer hours between posts",
        "`set ml [n]`        — Minimum likes",
        "`set ow [HH:MM - HH:MM]` — Online time window",
        "`set pr [h]`        — Twitter poll rate (hours)",
        "`set pn [n]`        — Tweets polled per scrape",
        "`set pc [n]`        — Post chance percent",
        "`set on [N - M]`    — Online min-max minutes",
        "`set of [N - M]`    — Offline min-max minutes",
        "",
        "**Admin**",
        "`adminme`           — Claim the boss role",
        "`kill` / `k`        — Shut the bot down",
    ]
    await message.channel.send("\n".join(lines))

# ---------------------------------------------------------------------------
# Command: show
# ---------------------------------------------------------------------------

async def handle_show(message: discord.Message, arg: str = "") -> None:
    qid = state.get("queued_post_id")
    msg = find_message_by_id(messages, qid)
    if msg is None:
        await message.channel.send(
            "Ey boss, I got nothin' queued up right now. Try 'scrape' or 'submit'."
        )
        return
    source_tag = f"[{msg['source']} | {msg['likes']} likes]"
    await message.channel.send(
        f"Ey boss, here's what I got lined up {source_tag}:\n\n{msg['text']}"
    )

# ---------------------------------------------------------------------------
# Command: config
# ---------------------------------------------------------------------------

async def handle_config(message: discord.Message, arg: str = "") -> None:
    display = {}
    for k, v in config.items():
        if k == "discord_token" and isinstance(v, str) and len(v) > 6:
            display[k] = v[:6] + "..."
        elif k == "twitter_password" and isinstance(v, str) and len(v) > 0:
            display[k] = "***"
        else:
            display[k] = v
    lines = [f"{k}: {v}" for k, v in display.items()]
    await message.channel.send("```\n" + "\n".join(lines) + "\n```")

# ---------------------------------------------------------------------------
# Command: list / more
# ---------------------------------------------------------------------------

async def _send_list_page(message: discord.Message, offset: int, count: int) -> None:
    min_likes = config.get("twitter_min_likes", 0)
    pool = get_unposted_messages(messages, min_likes)
    page = pool[offset:offset + count]
    if not page:
        await message.channel.send(
            "Ey boss, that's all I got — no more messages in the stash, capisce?"
        )
        return
    lines = []
    for i, m in enumerate(page, start=offset + 1):
        preview = m["text"].replace("\n", " ")  # todo I edited this, it should now show the whole tweet in the preview
        likes_str = f"{m['likes']} likes" if m["source"] == "scraped" else "custom"
        lines.append(f"`{i}.` [{likes_str}] {preview}…")
    total = len(pool)
    shown = min(offset + count, total)
    lines.append(f"\n_Showing {shown} of {total} postable messages._")
    await message.channel.send("\n".join(lines))


async def handle_list(message: discord.Message, arg: str = "") -> None:
    state["list_offset"] = 0
    save_state(state)
    await _send_list_page(message, 0, 5)


async def handle_more(message: discord.Message, arg: str = "") -> None:
    count = 5
    if arg.strip() in ("5", "10"):
        count = int(arg.strip())
    offset = state.get("list_offset", 0) + count
    state["list_offset"] = offset
    save_state(state)
    await _send_list_page(message, offset, count)

# ---------------------------------------------------------------------------
# Command: scrape
# ---------------------------------------------------------------------------

async def handle_scrape(message: discord.Message, arg: str = "") -> None:
    global _last_scrape_time
    await message.channel.send(
        "Ey boss, gimme a sec — I'm goin' out to get the goods..."
    )
    try:
        new_tweets = await _scrape(config)
    except Exception as e:
        await message.channel.send(f"It didn't work, boss. Error: {e}")
        return

    added = add_scraped_tweets(messages, new_tweets, config)
    _last_scrape_time = now_est()
    ensure_queued_post(state, config, messages)
    save_state(state)
    await message.channel.send(
        f"Done, boss! I picked up {len(new_tweets)} tweets — "
        f"{added} new one(s) added to the stash."
    )

# ---------------------------------------------------------------------------
# Command: skip
# ---------------------------------------------------------------------------

async def handle_skip(message: discord.Message, arg: str = "") -> None:
    current_id = state.get("queued_post_id")
    if current_id:
        msg = find_message_by_id(messages, current_id)
        if msg:
            msg["posted_to_discord"] = True
            save_messages(messages)

    state["queued_post_id"] = None
    if ensure_queued_post(state, config, messages):
        new_msg = find_message_by_id(messages, state["queued_post_id"])
        preview = (new_msg["text"][:80] + "…") if new_msg else "somethin' new"
        await message.channel.send(
            f"Skipped, boss. New one lined up:\n\n{preview}"
        )
    else:
        await message.channel.send(
            "Skipped, boss — but I got nothin' else to queue up right now."
        )

# ---------------------------------------------------------------------------
# Command: shuffle
# ---------------------------------------------------------------------------

async def handle_shuffle(message: discord.Message, arg: str = "") -> None:
    min_likes = config.get("twitter_min_likes", 0)
    current_id = state.get("queued_post_id")
    pool = get_unposted_messages(messages, min_likes)

    # Try to pick something different from the current
    candidates = [m for m in pool if m["id"] != current_id] if current_id else pool
    if not candidates:
        candidates = pool

    if not candidates:
        await message.channel.send(
            "Ey boss, nothin' else in the deck to shuffle to!"
        )
        return

    chosen = random.choice(candidates)
    state["queued_post_id"] = chosen["id"]
    save_state(state)
    preview = chosen["text"][:80] + "…"
    await message.channel.send(
        f"Shuffled, boss! How about this one:\n\n{preview}"
    )

# ---------------------------------------------------------------------------
# Command: pause
# ---------------------------------------------------------------------------

async def handle_pause(message: discord.Message, arg: str = "") -> None:
    state["paused"] = not state.get("paused", False)
    save_state(state)
    if state["paused"]:
        await message.channel.send(
            "Alright boss, I'm layin' low — no more postin' till you say so."
        )
    else:
        await message.channel.send(
            "We're back in business, boss! The operation's runnin' again."
        )

# ---------------------------------------------------------------------------
# Command: nextpost
# ---------------------------------------------------------------------------

async def handle_nextpost(message: discord.Message, arg: str = "") -> None:
    npt = state.get("next_post_time")
    if not npt:
        await message.channel.send(
            "Ey boss, I ain't got a next post time set yet. Try 'scrape' to load some tweets."
        )
        return
    try:
        dt = datetime.fromisoformat(npt)
        formatted = dt.strftime("%A, %B %d at %I:%M %p %Z")
    except Exception:
        formatted = npt

    paused_note = " (but we're paused right now, boss!)" if state.get("paused") else ""
    qid = state.get("queued_post_id")
    queued_note = " Nothing queued yet." if not qid else ""
    await message.channel.send(
        f"Next shot's on {formatted}{paused_note}.{queued_note}"
    )

# ---------------------------------------------------------------------------
# Command: submit
# ---------------------------------------------------------------------------

async def handle_submit(message: discord.Message, arg: str = "") -> None:
    if not arg.strip():
        await message.channel.send(
            "Ey boss, you gotta give me the text to add!"
        )
        return
    success, reply = add_custom_message(messages, arg)
    await message.channel.send(reply)
    if success:
        ensure_queued_post(state, config, messages)
        save_state(state)

# ---------------------------------------------------------------------------
# Command: type
# ---------------------------------------------------------------------------

async def handle_type(message: discord.Message, arg: str = "") -> None:
    if not arg.strip():
        await message.channel.send(
            "Ey boss, you gotta give me somethin' to say!"
        )
        return
    try:
        channel = await get_post_channel(client, config)
        await send_with_typing(channel, arg)
        await message.channel.send("Done, boss! Slipped it right in there, nice and smooth.")
    except Exception as e:
        await message.channel.send(f"It didn't work, boss: {e}")

# ---------------------------------------------------------------------------
# Command: set
# ---------------------------------------------------------------------------

async def handle_set(message: discord.Message, arg: str = "") -> None:
    parts = arg.split(maxsplit=1)
    if len(parts) < 2:
        await message.channel.send(
            "It didn't work, boss. Format: `set XX value` — try `help` for the list."
        )
        return

    sub = parts[0].lower()
    value = parts[1].strip()

    try:
        if sub == "ch":
            config["discord_channel_id"] = int(value)
            save_config(config)
            await message.channel.send(f"You got it, boss. Channel is now `{value}`.")

        elif sub == "ti":
            config["post_interval_days"] = int(value)
            save_config(config)
            await message.channel.send(f"You got it, boss. Posting every `{value}` day(s).")

        elif sub == "wi":
            start, end = parse_window(value)
            config["post_window_start"] = start
            config["post_window_end"] = end
            save_config(config)
            await message.channel.send(f"You got it, boss. Post window is `{start} – {end}`.")

        elif sub == "sp":
            config["post_spacer_hours"] = float(value)
            save_config(config)
            await message.channel.send(f"You got it, boss. Spacer is `{value}` hour(s).")

        elif sub == "ml":
            config["twitter_min_likes"] = int(value)
            save_config(config)
            await message.channel.send(f"You got it, boss. Min likes is `{value}`.")

        elif sub == "ow":
            start, end = parse_window(value)
            config["online_window_start"] = start
            config["online_window_end"] = end
            save_config(config)
            await message.channel.send(f"You got it, boss. Online window is `{start} – {end}`.")

        elif sub == "pr":
            config["twitter_poll_rate_hours"] = int(value)
            save_config(config)
            await message.channel.send(f"You got it, boss. Scraping every `{value}` hour(s).")

        elif sub == "pn":
            config["twitter_poll_count"] = min(int(value), 40)
            save_config(config)
            await message.channel.send(
                f"You got it, boss. Polling `{config['twitter_poll_count']}` tweets per scrape."
            )

        elif sub == "pc":
            v = int(value)
            if not 0 <= v <= 100:
                raise ValueError("Must be 0–100")
            config["post_chance_percent"] = v
            save_config(config)
            await message.channel.send(f"You got it, boss. Post chance is `{v}%`.")

        elif sub == "on":
            lo, hi = parse_minmax(value)
            config["online_min_minutes"] = lo
            config["online_max_minutes"] = hi
            save_config(config)
            await message.channel.send(
                f"You got it, boss. Online duration is `{lo}–{hi}` minutes."
            )

        elif sub == "of":
            lo, hi = parse_minmax(value)
            config["offline_min_minutes"] = lo
            config["offline_max_minutes"] = hi
            save_config(config)
            await message.channel.send(
                f"You got it, boss. Offline duration is `{lo}–{hi}` minutes."
            )

        else:
            await message.channel.send(
                "It didn't work, boss — I don't know that set command. Try `help`."
            )

    except (ValueError, TypeError):
        await message.channel.send("It didn't work, boss. Check the format and try again.")


async def handle_get(message: discord.Message, arg: str = "") -> None:
    sub = arg.strip().lower()

    getters = {
        "ch":  ("discord_channel_id",    lambda v: f"`{v}`"),
        "ti":  ("post_interval_days",     lambda v: f"`{v}` day(s)"),
        "wi":  (None,                     lambda _: f"`{config.get('post_window_start', '?')} – {config.get('post_window_end', '?')}`"),
        "sp":  ("post_spacer_hours",      lambda v: f"`{v}` hour(s)"),
        "ml":  ("twitter_min_likes",      lambda v: f"`{v}` likes"),
        "ow":  (None,                     lambda _: f"`{config.get('online_window_start', '?')} – {config.get('online_window_end', '?')}`"),
        "pr":  ("twitter_poll_rate_hours",lambda v: f"`{v}` hour(s)"),
        "pn":  ("twitter_poll_count",     lambda v: f"`{v}` tweets per scrape"),
        "pc":  ("post_chance_percent",    lambda v: f"`{v}%`"),
        "on":  (None,                     lambda _: f"`{config.get('online_min_minutes', '?')}–{config.get('online_max_minutes', '?')}` minutes"),
        "of":  (None,                     lambda _: f"`{config.get('offline_min_minutes', '?')}–{config.get('offline_max_minutes', '?')}` minutes"),
    }

    labels = {
        "ch": "Channel ID",
        "ti": "Post interval",
        "wi": "Post window",
        "sp": "Spacer hours",
        "ml": "Min likes",
        "ow": "Online window",
        "pr": "Twitter poll rate",
        "pn": "Tweets per scrape",
        "pc": "Post chance",
        "on": "Online duration",
        "of": "Offline duration",
    }

    if sub and sub in getters:
        key, formatter = getters[sub]
        value = config.get(key) if key else None
        await message.channel.send(f"{labels[sub]}: {formatter(value)}")
        return

    if sub and sub not in getters:
        await message.channel.send(
            "It didn't work, boss — I don't know that get command. Try `help` or just `get` for everything."
        )
        return

    # No arg — show all
    lines = ["Ey boss, here's how everything's set right now:", ""]
    for code, (key, formatter) in getters.items():
        value = config.get(key) if key else None
        lines.append(f"`{code}` {labels[code]}: {formatter(value)}")
    await message.channel.send("\n".join(lines))

# ---------------------------------------------------------------------------
# Command: kill
# ---------------------------------------------------------------------------

async def handle_kill(message: discord.Message, arg: str = "") -> None:
    await message.channel.send(
        "Alright boss, I'm goin' dark. It's been an honor workin' for ya. "
        "Don't let 'em find the stash."
    )
    await client.close()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    config = load_config()
    state = load_state()
    messages = load_messages()
    client.run(config["discord_token"])
