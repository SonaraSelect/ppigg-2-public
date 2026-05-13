import os
import json
from twikit import Client


async def authenticate(config: dict) -> Client:
    client = Client(language="en-US")
    cookies_path = config.get("twitter_cookies_file", "cookies.json")
    raw_cookies_path = config.get("twitter_raw_cookies_file", "cookies_raw.json")

    # Try raw Cookie-Editor export first (list of dicts)
    if os.path.exists(raw_cookies_path):
        print(f"Loading cookies from {raw_cookies_path}")
        with open(raw_cookies_path) as f:
            data = json.load(f)
        cookies = {c["name"]: c["value"] for c in data} if isinstance(data, list) else data
        client.set_cookies(cookies)
        return client

    # Try twikit's own saved cookie format
    if os.path.exists(cookies_path):
        print(f"Loading cookies from {cookies_path}")
        client.load_cookies(cookies_path)
        return client

    # Fallback: programmatic login
    print("No cookie file found, attempting programmatic login...")
    try:
        await client.login(
            auth_info_1=config["twitter_username"],
            auth_info_2=config["twitter_email"],
            password=config["twitter_password"],
        )
        client.save_cookies(cookies_path)
        print(f"Login successful. Cookies saved to {cookies_path}")
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
    client = await authenticate(config)

    print("Authenticate complete!")

    print(f"Fetching user: {config['twitter_target_user']}")
    user = await client.get_user_by_screen_name(config["twitter_target_user"])

    count = min(int(config.get("twitter_poll_count", 40)), 40)
    print(f"Fetching {count} tweets...")

    results = await client.get_user_tweets(user.id, tweet_type="Tweets", count=count)

    tweets = [
        {"id": tweet.id, "text": tweet.text, "likes": tweet.favorite_count}
        for tweet in results
        if tweet.retweeted_tweet is None
    ]

    print(f"Fetched {len(tweets)} tweets from @{config['twitter_target_user']}")
    return tweets