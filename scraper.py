import re
import time
import requests
from models import Post
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from cleaning import clean_news_text
from datetime import datetime, timezone
import tomllib
from collections import defaultdict

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
BACKGROUND_URL = re.compile(r"url\(['\"]?([^'\")]+)['\"]?\)")


with open("config.toml", "rb") as f:
    config = tomllib.load(f)

def make_session():
    """HTTP session that retries on temporary server errors."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    retry = Retry(total=3, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def extract_image_urls(msg):
    """Photo URLs and video thumbnail URLs of one post, in display order.

    The web preview stores them as CSS: style="background-image:url('...')".
    """
    selectors = (
        "a.tgme_widget_message_photo_wrap",        # photos (also albums)
        "i.tgme_widget_message_video_thumb",       # video preview frame
        "i.tgme_widget_message_roundvideo_thumb",  # round video preview frame
    )
    urls = []
    for element in msg.select(", ".join(selectors)):
        match = BACKGROUND_URL.search(element.get("style", ""))
        if match and match.group(1) not in urls:
            urls.append(match.group(1))
    return urls


def parse_post(msg, channel):
    """Turn one message <div> into a (date, Post) pair, or None if unusable."""
    time_tag = msg.find("time", class_="time")
    if not time_tag or not time_tag.has_attr("datetime"):
        return None
    data_post = msg.get("data-post", "")          # e.g. "znua_live/12345"
    if "/" not in data_post or not data_post.rsplit("/", 1)[-1].isdigit():
        return None
    post_id = int(data_post.rsplit("/", 1)[-1])

    text_div = msg.find("div", class_="tgme_widget_message_text")
    if not text_div:
        return None
    for br in text_div.find_all("br"):
        br.replace_with("\n")
    text = clean_news_text(text_div.get_text())
    if not text:
        return None

    msg_date = datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00"))
    post = Post(
        post_id=post_id,
        time=msg_date.strftime("%H:%M"),
        text=text,
        url=f"https://t.me/{channel}/{post_id}",
        image_urls=extract_image_urls(msg),
    )
    return msg_date, post


def fetch_posts(session, channel, since, processed_dates):
    """Walk the channel preview from newest to oldest until `since` or a known day."""
    print("Connecting to the Telegram web preview (looking for new posts)...")
    daily_posts = defaultdict(list)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_url = f"https://t.me/s/{channel}"
    count = 0

    while current_url:
        try:
            response = session.get(current_url, timeout=config["start"]["REQUEST_TIMEOUT"])
            response.raise_for_status()
        except requests.RequestException as error:
            print(f"Could not load page: {error}")
            break

        soup = BeautifulSoup(response.text, "html.parser")
        messages = soup.find_all("div", class_="tgme_widget_message")
        if not messages:
            break

        reached_end = False
        for msg in reversed(messages):               # newest first
            parsed = parse_post(msg, channel)
            if parsed is None:
                continue
            msg_date, post = parsed
            date_str = msg_date.strftime("%Y-%m-%d")

            if (date_str in processed_dates and date_str != today_str) or msg_date < since:
                reached_end = True
                break

            daily_posts[date_str].append(post)
            count += 1
            if count % 50 == 0:
                print(f"  {count} new posts loaded...")

        if reached_end:
            break
        more_link = soup.find("a", class_="tme_messages_more")
        if not (more_link and more_link.has_attr("href")):
            break
        current_url = "https://t.me" + more_link["href"]
        time.sleep(config["start"]["REQUEST_DELAY"])

    print(f"Done. New posts found: {count}")
    return daily_posts
