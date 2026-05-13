import json
import os

CONFIG_FILE = "config.json"
STATE_FILE = "state.json"
MESSAGES_FILE = "saved_messages.json"


def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    try:
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp, CONFIG_FILE)
    except Exception as e:
        print(f"[warn] Could not save config: {e}")


def _default_state() -> dict:
    return {
        "next_post_time": None,
        "queued_post_id": None,
        "paused": False,
        "last_post_time": None,
        "list_offset": 0,
    }


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        defaults = _default_state()
        defaults.update(data)
        return defaults
    except Exception:
        return _default_state()


def save_state(state: dict) -> None:
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[warn] Could not save state: {e}")


def load_messages() -> list[dict]:
    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_messages(messages: list[dict]) -> None:
    try:
        tmp = MESSAGES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(messages, f, indent=2, ensure_ascii=False)
        os.replace(tmp, MESSAGES_FILE)
    except Exception as e:
        print(f"[warn] Could not save messages: {e}")
