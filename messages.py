import random
import uuid

from storage import save_messages
from time_utils import now_est


def get_unposted_messages(messages: list[dict], min_likes: int) -> list[dict]:
    return [
        m for m in messages
        if not m.get("posted_to_discord") and m.get("likes", 0) >= min_likes
    ]


def pick_random_unposted(messages: list[dict], min_likes: int) -> dict | None:
    pool = get_unposted_messages(messages, min_likes)
    return random.choice(pool) if pool else None


def find_message_by_id(messages: list[dict], msg_id: str | None) -> dict | None:
    if not msg_id:
        return None
    for m in messages:
        if m["id"] == msg_id:
            return m
    return None


def add_scraped_tweets(messages: list[dict], new_tweets: list[dict], config: dict) -> int:
    min_likes = config.get("twitter_min_likes", 0)
    changed = False
    added = 0
    for tweet in new_tweets:
        existing = find_message_by_id(messages, tweet["id"])
        if existing:
            if existing["likes"] != tweet["likes"]:
                existing["likes"] = tweet["likes"]
                changed = True
        else:
            if tweet["likes"] >= min_likes:
                messages.append({
                    "id": tweet["id"],
                    "text": tweet["text"],
                    "likes": tweet["likes"],
                    "source": "scraped",
                    "posted_to_discord": False,
                    "added_at": now_est().isoformat(),
                })
                added += 1
                changed = True
    if changed:
        save_messages(messages)
    return added


def add_custom_message(messages: list[dict], text: str) -> tuple[bool, str]:
    text_stripped = text.strip()
    text_lower = text_stripped.lower()
    for m in messages:
        if m.get("text", "").lower() == text_lower:
            return False, "Ey boss, I already got that one in the stash!"
    messages.append({
        "id": str(uuid.uuid4()),
        "text": text_stripped,
        "likes": 0,
        "source": "custom",
        "posted_to_discord": False,
        "added_at": now_est().isoformat(),
    })
    save_messages(messages)
    return True, "Done, boss! Added it to the stash, nice and clean."
