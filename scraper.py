import os
import json
import traceback

# MONKEY PATCH: Remove when twikit fixes ON_DEMAND_FILE_REGEX
import re
_tx_mod = __import__('twikit.x_client_transaction.transaction', fromlist=['ClientTransaction'])
_tx_mod.ON_DEMAND_FILE_REGEX = re.compile(
    r""",(\d+):["']ondemand\.s["']""", flags=(re.VERBOSE | re.MULTILINE))
_tx_mod.ON_DEMAND_HASH_PATTERN = r',{}:"([0-9a-f]+)"'

async def _patched_get_indices(self, home_page_response, session, headers):
    key_byte_indices = []
    response = self.validate_response(home_page_response) or self.home_page_response
    on_demand_file_index = _tx_mod.ON_DEMAND_FILE_REGEX.search(str(response)).group(1)
    regex = re.compile(_tx_mod.ON_DEMAND_HASH_PATTERN.format(on_demand_file_index))
    filename = regex.search(str(response)).group(1)
    on_demand_file_url = f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{filename}a.js"
    on_demand_file_response = await session.request(method="GET", url=on_demand_file_url, headers=headers)
    key_byte_indices_match = _tx_mod.INDICES_REGEX.finditer(str(on_demand_file_response.text))
    for item in key_byte_indices_match:
        key_byte_indices.append(item.group(2))
    if not key_byte_indices:
        raise Exception("Couldn't get KEY_BYTE indices")
    key_byte_indices = list(map(int, key_byte_indices))
    return key_byte_indices[0], key_byte_indices[1:]

_tx_mod.ClientTransaction.get_indices = _patched_get_indices
# END MONKEY PATCH

import twikit
from twikit import Client


async def authenticate(config: dict) -> Client:
    client = Client(language="en-US")
    cookies_path = config.get("twitter_cookies_file", "cookies.json")
    raw_cookies_path = config.get("twitter_raw_cookies_file", "cookies_raw.json")

    # Try raw Cookie-Editor export first (list of dicts)
    if os.path.exists(raw_cookies_path):
        print(f"[twikit] Loading cookies from {raw_cookies_path}")
        with open(raw_cookies_path) as f:
            data = json.load(f)
        cookies = {c["name"]: c["value"] for c in data} if isinstance(data, list) else data
        client.set_cookies(cookies)
        print(f"[twikit] Loaded {len(cookies)} cookie(s): {list(cookies.keys())}")
        return client

    # Try twikit's own saved cookie format
    if os.path.exists(cookies_path):
        print(f"[twikit] Loading cookies from {cookies_path}")
        client.load_cookies(cookies_path)
        try:
            cookie_keys = list(client.http.cookies.keys())
            print(f"[twikit] Loaded {len(cookie_keys)} cookie(s): {cookie_keys}")
        except Exception:
            print("[twikit] (couldn't inspect cookie jar)")
        return client

    # Fallback: programmatic login
    print("[twikit] No cookie file found, attempting programmatic login...")
    try:
        await client.login(
            auth_info_1=config["twitter_username"],
            auth_info_2=config["twitter_email"],
            password=config["twitter_password"],
        )
        client.save_cookies(cookies_path)
        print(f"[twikit] Login successful. Cookies saved to {cookies_path}")
    except Exception as e:
        raise RuntimeError(
            f"Programmatic login failed: {e}\n\n"
            "To fix this:\n"
            "  1. Log into x.com in your browser\n"
            "  2. Export cookies via the Cookie-Editor extension as JSON\n"
            f"  3. Save the file as '{raw_cookies_path}' in this directory\n"
            "  4. Re-run the scraper\n"
        ) from e

    return client


async def scrape_tweets(config: dict) -> list[dict]:
    print(f"[twikit] version: {twikit.__version__}")
    client = await authenticate(config)
    print("[twikit] Authenticate complete!")

    target = config["twitter_target_user"]
    print(f"[twikit] Step: get_user_by_screen_name({target!r})")
    try:
        user = await client.get_user_by_screen_name(target)
        print(f"[twikit] User found: id={user.id}")
    except Exception as e:
        print(f"[twikit] get_user_by_screen_name FAILED: {type(e).__name__}: {e}")
        print(traceback.format_exc())
        raise

    count = min(int(config.get("twitter_poll_count", 40)), 40)
    print(f"[twikit] Step: get_user_tweets(user_id={user.id}, count={count})")
    try:
        results = await client.get_user_tweets(user.id, tweet_type="Tweets", count=count)
        print(f"[twikit] Fetched {len(results)} tweet(s) from @{target}")
    except Exception as e:
        print(f"[twikit] get_user_tweets FAILED: {type(e).__name__}: {e}")
        print(traceback.format_exc())
        raise

    return [
        {"id": tweet.id, "text": tweet.text, "likes": tweet.favorite_count}
        for tweet in results
    ]


if __name__ == "__main__":
    import asyncio
    from storage import load_config
    cfg = load_config()
    tweets = asyncio.run(scrape_tweets(cfg))
    print(f"[twikit] Done. {len(tweets)} tweet(s) returned.")
    for t in tweets[:3]:
        print(f"  [{t['likes']} likes] {t['text'][:80]}")