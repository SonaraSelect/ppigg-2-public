import asyncio
from datetime import datetime, timezone

import discord


async def send_with_typing(channel: discord.TextChannel, content: str) -> None:
    delay = len(content) * 0.1
    async with channel.typing():
        await asyncio.sleep(delay)
    await channel.send(content)


async def notify_admin(client: discord.Client, config: dict, message: str) -> None:
    try:
        admin_id = config.get("admin_user_id", 0)
        if not admin_id:
            print(f"[notify_admin] No admin set. Message: {message}")
            return
        user = await client.fetch_user(int(admin_id))
        dm = await user.create_dm()
        await dm.send(message)
    except Exception as e:
        print(f"[notify_admin] Failed to reach admin: {e}. Message was: {message}")


async def hours_since_last_channel_message(channel) -> float | None:
    try:
        async for msg in channel.history(limit=1):
            now_utc = datetime.now(tz=timezone.utc)
            delta = now_utc - msg.created_at.replace(tzinfo=timezone.utc) if msg.created_at.tzinfo is None else now_utc - msg.created_at
            return delta.total_seconds() / 3600
    except Exception:
        pass
    return None


async def get_post_channel(client: discord.Client, config: dict):
    channel_id = int(config.get("discord_channel_id", 0))
    channel = client.get_channel(channel_id)
    if channel is None:
        channel = await client.fetch_channel(channel_id)
    return channel
