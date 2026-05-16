from twscrape import API, gather


async def scrape_tweets(config: dict) -> list[dict]:
    db_path = config.get("twscrape_db_path", "twscrape_accounts.db")
    api = API(db_path)

    username = config.get("twscrape_username")
    if username:
        try:
            await api.pool.add_account(
                username=username,
                password=config.get("twscrape_password", ""),
                email=config.get("twscrape_email", ""),
                email_password=config.get("twscrape_email_password", ""),
                cookies=config.get("twscrape_cookies") or "",
            )
        except Exception:
            pass  # account already exists in db
        await api.pool.login_all()

    target = config["twitter_target_user"]
    count = min(int(config.get("twitter_poll_count", 40)), 40)

    print(f"[twscrape] Fetching user: {target}")
    user = await api.user_by_login(target)

    print(f"[twscrape] Fetching {count} tweets...")
    results = await gather(api.user_tweets(user.id, limit=count))

    tweets = [
        {"id": str(tweet.id), "text": tweet.rawContent, "likes": tweet.likeCount}
        for tweet in results
        if tweet.retweetedTweet is None
    ]
    print(f"[twscrape] Fetched {len(tweets)} tweets from @{target}")
    return tweets
