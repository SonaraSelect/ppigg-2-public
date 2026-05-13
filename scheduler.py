from datetime import datetime, timedelta

from storage import save_state
from time_utils import now_est, make_est_datetime
from messages import find_message_by_id, pick_random_unposted


def calculate_next_post_time(config: dict, from_time: datetime | None = None) -> datetime:
    base = from_time or now_est()
    interval = int(config.get("post_interval_days", 1))
    next_date = (base + timedelta(days=interval)).date()
    return make_est_datetime(next_date, config.get("post_window_start", "10:00"))


def is_post_day(state: dict) -> bool:
    npt = state.get("next_post_time")
    if not npt:
        return False
    try:
        dt = datetime.fromisoformat(npt)
        return now_est().date() >= dt.date()
    except Exception:
        return False


def ensure_queued_post(state: dict, config: dict, messages: list[dict]) -> bool:
    qid = state.get("queued_post_id")
    msg = find_message_by_id(messages, qid)
    if msg and not msg.get("posted_to_discord"):
        return True
    # Need a new post
    min_likes = config.get("twitter_min_likes", 0)
    chosen = pick_random_unposted(messages, min_likes)
    if chosen:
        state["queued_post_id"] = chosen["id"]
        save_state(state)
        return True
    state["queued_post_id"] = None
    save_state(state)
    return False
