"""Telegram channel digest.

Scrapes the public web preview of a Telegram channel (t.me/s/<channel>), lets a
local LLM (Ollama) rate the posts by importance and writes a Markdown digest.
Every highlighted post is shown like in Telegram: photo / video thumbnail first,
then the original text, plus a short analysis and a link to the original post.
The digest is written as Markdown and also converted to a PDF with the images embedded.

Extra package for the PDF:  pip install markdown   (uses Microsoft Edge / Chrome to print the PDF)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import ollama
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==================== DEFAULTS (override via command line) ====================
DEFAULT_CHANNEL = "znua_live"
DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_SINCE = "2026-07-09"
DEFAULT_OUTPUT = "news_digest_ongoing.md"
DEFAULT_IMAGES_DIR = "images"
DEFAULT_MIN_IMPORTANCE = 4      # 1-5, only posts rated at least this high are kept
DEFAULT_THINK = True          # True = model reasons before answering (slower, maybe better)
DEFAULT_MAKE_PDF = True         # True = also create a PDF with the images embedded
DEFAULT_PDF = None              # PDF file name; None = same name as DEFAULT_OUTPUT (.pdf)
DEFAULT_DELETE_MD = True       # True = delete the .md after the PDF was made (PDF gets a date in its name)

BATCH_SIZE = 15                 # posts per LLM call
MAX_EVENTS_PER_BATCH = 3
MAX_CHARS_PER_POST = 1000        # posts are shortened before they go to the LLM
REQUEST_TIMEOUT = 15            # seconds
REQUEST_DELAY = 1.0             # pause between two page requests
# ==============================================================================

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
BACKGROUND_URL = re.compile(r"url\(['\"]?([^'\")]+)['\"]?\)")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# The LLM must answer in this JSON shape (enforced by Ollama's structured output).
EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "importance": {"type": "integer"},
                    "analysis": {"type": "string"},
                },
                "required": ["id", "importance", "analysis"],
            },
        }
    },
    "required": ["events"],
}


@dataclass
class Post:
    post_id: int
    time: str                   # "HH:MM" (UTC)
    text: str
    url: str
    image_urls: list = field(default_factory=list)   # photos and video thumbnails


# ------------------------------------------------------------------ scraping --

def clean_news_text(raw_text):
    """Remove polls, channel boilerplate and extra blank lines."""
    if not raw_text:
        return ""
    if "[ Poll :" in raw_text or "Ви вважаєте, що" in raw_text:
        return ""

    header_match = re.search(r"\|\s*[\w\s]+:\s*(.*)", raw_text, re.DOTALL)
    text_content = header_match.group(1) if header_match else raw_text

    garbage_patterns = [
        r"🚀Підтримати🤝_✅✅_🤝",
        r"🚀Приєднатися до ZN\.UA",
        r"Читати на русском\.",
        r"Детальніше про них читайте на нашому сайті\.",
        r"Читать на русском",
    ]
    for pattern in garbage_patterns:
        text_content = re.sub(pattern, "", text_content, flags=re.IGNORECASE)

    text_content = re.sub(r"\n{3,}", "\n\n", text_content)
    clean_text = text_content.strip()
    return clean_text if len(clean_text) > 20 else ""


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
            response = session.get(current_url, timeout=REQUEST_TIMEOUT)
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
        time.sleep(REQUEST_DELAY)

    print(f"Done. New posts found: {count}")
    return daily_posts


# ----------------------------------------------------------------- LLM part --

def chunk_list(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def pick_events(batch, model, min_importance, think=False):
    """Ask the LLM which posts of the batch matter. Returns [(post, importance, analysis)].

    The model only returns post IDs; the text shown in the digest is always the
    original post, so the model cannot invent or alter quotes.
    """
    by_id = {post.post_id: post for post in batch}
    listing = "\n\n".join(
        f"[ID:{post.post_id} | {post.time}] {post.text[:MAX_CHARS_PER_POST]}" for post in batch
    )
    prompt = f"""You rate news posts by strategic importance.
Each post below starts with [ID:<number> | <time>].

Scale:
5 = major war development, major geopolitical decision, national-scale incident
4 = important political or economic news with national impact
3 or lower = minor local news, advertising, opinions, human-interest stories

Return only posts rated {min_importance} or higher, at most {MAX_EVENTS_PER_BATCH}.
If no post qualifies, return an empty "events" list.
For every returned post:
- "id": copy the number from the post header exactly
- "importance": your rating (integer)
- "analysis": ONE short sentence in UKRAINIAN explaining why it is strategically important

<posts>
{listing}
</posts>"""

    messages = [
        {"role": "system", "content": "You are a precise news-rating filter. Answer only with the requested JSON."},
        {"role": "user", "content": prompt},
    ]
    chat_args = dict(
        model=model,
        messages=messages,
        format=EVENT_SCHEMA,
        # with thinking on, the reasoning tokens count against num_predict, so allow much more
        options={"temperature": 0.0, "num_ctx": 8192, "num_predict": 4000 if think else 1500},
    )
    try:
        # Thinking models (gemma4, qwen3, ...) can use up the token budget on hidden
        # reasoning and return an empty answer, so thinking is off unless --think is given.
        response = ollama.chat(think=think, **chat_args)
    except TypeError:                                 # old ollama python package: no `think` option
        response = ollama.chat(**chat_args)

    content = (response["message"]["content"] or "").strip()
    if not content:
        raise ValueError("the model returned an empty answer (try --model gemma2 or update Ollama)")
    data = json.loads(content)

    events, seen = [], set()
    for item in data.get("events", []):
        post_id, importance, analysis = item.get("id"), item.get("importance"), item.get("analysis")
        # Reject hallucinated IDs, duplicates and weak events.
        if post_id not in by_id or post_id in seen:
            continue
        if not isinstance(importance, int) or importance < min_importance:
            continue
        if not isinstance(analysis, str) or not analysis.strip():
            continue
        seen.add(post_id)
        events.append((by_id[post_id], min(importance, 5), analysis.strip()))

    events.sort(key=lambda e: e[1], reverse=True)
    return events[:MAX_EVENTS_PER_BATCH]


# ------------------------------------------------------------------- output --

def download_image(session, url, images_dir, stem):
    """Save one image and return its path (None if the download failed)."""
    extension = Path(urlparse(url).path).suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        extension = ".jpg"
    path = images_dir / f"{stem}{extension}"
    if path.exists():
        return path
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as error:
        print(f"    Could not download image: {error}")
        return None
    path.write_bytes(response.content)
    return path


def render_event(post, importance, analysis, image_paths, output_dir):
    """Markdown for one post, laid out like in Telegram: images, headline, text."""
    lines = []
    for path in image_paths:
        relative = os.path.relpath(path, output_dir).replace(os.sep, "/")
        lines.append(f"![]({relative})")
    lines.append("")
    lines.append(f"🕒 {post.time} · ⭐ {importance}/5 · [Telegram]({post.url})")
    lines.append("")

    paragraphs = post.text.split("\n\n")
    lines.append(f"**{paragraphs[0].strip().replace(chr(10), ' ')}**")     # first paragraph is bold
    for paragraph in paragraphs[1:]:
        lines.append("")
        lines.append(paragraph.strip().replace("\n", "  \n"))
    lines.append("")
    lines.append(f"* *Аналіз:* {analysis}")
    return "\n".join(lines)


def generate_daily_summary(session, date, posts, args, channel):
    posts.sort(key=lambda p: p.post_id)               # chronological
    batches = list(chunk_list(posts, BATCH_SIZE))
    print(f"AI analysis for {date}: {len(posts)} posts, {len(batches)} batches")

    output_dir = Path(args.output).resolve().parent
    images_dir = Path(args.images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    rendered = []
    failed_batches = 0
    for index, batch in enumerate(batches, start=1):
        print(f"  Batch {index}/{len(batches)}...")
        try:
            events = pick_events(batch, args.model, args.min_importance, args.think)
        except Exception as error:                    # one bad batch must not stop the run
            print(f"  Batch {index} failed: {error}")
            failed_batches += 1
            continue
        for post, importance, analysis in events:
            image_paths = []
            for number, url in enumerate(post.image_urls, start=1):
                path = download_image(session, url, images_dir, f"{channel}_{post.post_id}_{number}")
                if path:
                    image_paths.append(path.resolve())
            rendered.append((post.post_id, render_event(post, importance, analysis, image_paths, output_dir)))

    if failed_batches:
        print(f"  {failed_batches} batch(es) failed, so {date} is NOT saved and will be retried next run.")
        return None

    if not rendered:
        return "Не зафіксовано критично важливих макроподій у цей день."

    rendered.sort(key=lambda item: item[0])
    print(f"  Report for {date} ready ({len(rendered)} key events).")
    return "### 🚀 Головні події дня\n\n" + "\n\n".join(text for _, text in rendered)


# ---------------------------------------------------------------------- pdf --

PDF_CSS = """
@page { size: A4; margin: 18mm 15mm; }
body { font-family: "Segoe UI", "Segoe UI Emoji", Arial, "DejaVu Sans", sans-serif; font-size: 10.5pt; line-height: 1.45; color: #111; }
h1 { font-size: 20pt; border-bottom: 2px solid #333; padding-bottom: 4px; }
h2 { font-size: 15pt; margin-top: 22px; background: #eef2f7; padding: 5px 8px; border-radius: 4px; }
h3 { font-size: 12.5pt; margin-top: 14px; }
img { display: block; max-width: 100%; max-height: 90mm; margin: 6px 0; border-radius: 4px; }
p { margin: 6px 0; }
ul { margin: 6px 0 14px 18px; }
a { color: #1a5fb4; text-decoration: none; }
hr { border: 0; border-top: 1px solid #ccc; margin: 16px 0; }
"""


def find_browser():
    """Locate Microsoft Edge / Chrome (Edge is preinstalled on Windows)."""
    for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    for var in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidates += [
                os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
            ]
    candidates += [
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next((c for c in candidates if os.path.exists(c)), None)


def convert_to_pdf(md_path, pdf_path):
    """Convert the Markdown digest (with local images) into a PDF.

    Markdown -> HTML (python-markdown) -> PDF via headless Edge/Chrome.
    Only needs:  pip install markdown
    """
    try:
        import markdown
    except ImportError as error:
        print(f"PDF skipped: {error}\n  Install with:  python -m pip install markdown")
        return False

    browser = find_browser()
    if not browser:
        print("PDF skipped: Microsoft Edge or Google Chrome was not found.")
        return False

    md_path = Path(md_path).resolve()
    pdf_path = Path(pdf_path).resolve()
    body = markdown.markdown(md_path.read_text(encoding="utf-8"),
                             extensions=["extra", "sane_lists", "nl2br"])
    html = (
        f'<!DOCTYPE html><html lang="uk"><head><meta charset="utf-8">'
        f"<style>{PDF_CSS}</style></head><body>{body}</body></html>"
    )
    # The HTML sits next to the .md so relative image paths ("images/x.jpg") keep working.
    html_path = md_path.with_name(md_path.stem + "_pdf_temp.html")
    html_path.write_text(html, encoding="utf-8")

    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:       # fresh profile: works even if Edge is open
            command = [
                browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={pdf_path}",
                html_path.as_uri(),
            ]
            subprocess.run(command, check=True, timeout=180,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError) as error:
        print(f"PDF failed: {error}")
        return False
    finally:
        html_path.unlink(missing_ok=True)

    if not pdf_path.exists():
        print("PDF failed: the browser did not create a file.")
        return False
    print(f"PDF written: '{pdf_path}'")
    return True


# --------------------------------------------------------------------- main --

def parse_args():
    parser = argparse.ArgumentParser(description="AI digest of a public Telegram channel.")
    parser.add_argument("--channel", default=DEFAULT_CHANNEL, help="channel username without @")
    parser.add_argument("--since", default=DEFAULT_SINCE, help="first day to process, YYYY-MM-DD")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name")
    parser.add_argument("--min-importance", type=int, default=DEFAULT_MIN_IMPORTANCE, choices=range(1, 6))
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Markdown file (appended to)")
    parser.add_argument("--images-dir", default=DEFAULT_IMAGES_DIR, help="folder for downloaded images")
    parser.add_argument("--pdf", default=DEFAULT_PDF, help="PDF file (default: same name as --output, .pdf)")
    parser.add_argument("--make-pdf", dest="make_pdf", action="store_true", help="create a PDF")
    parser.add_argument("--no-pdf", dest="make_pdf", action="store_false", help="do not create a PDF")
    parser.add_argument("--think", action=argparse.BooleanOptionalAction, default=DEFAULT_THINK,
                        help="let the model reason before answering (--no-think to switch off)")
    parser.add_argument("--delete-md", action=argparse.BooleanOptionalAction, default=DEFAULT_DELETE_MD,
                        help="delete the Markdown file after the PDF was created (--no-delete-md to keep it)")
    parser.set_defaults(make_pdf=DEFAULT_MAKE_PDF)
    return parser.parse_args()


def pdf_target(args, today_str):
    """PDF path: --pdf if given; with --delete-md a dated name so runs do not overwrite each other."""
    if args.pdf:
        return args.pdf
    md = Path(args.output)
    if args.delete_md:
        return md.with_name(f"{md.stem}_{today_str}.pdf")
    return md.with_suffix(".pdf")


def main():
    args = parse_args()
    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    processed_dates = set()
    if os.path.exists(args.output):
        with open(args.output, "r", encoding="utf-8") as f:
            processed_dates = set(re.findall(r"## 📅 Підсумок за (\d{4}-\d{2}-\d{2})", f.read()))
        if processed_dates:
            print(f"Found {len(processed_dates)} days that are already processed.")

    session = make_session()
    daily_posts = fetch_posts(session, args.channel, since, processed_dates)
    if not daily_posts:
        print("Nothing new to analyse.")
        if args.make_pdf and os.path.exists(args.output):
            convert_to_pdf(args.output, pdf_target(args, today_str))
        return

    with open(args.output, "a", encoding="utf-8") as f:
        if not processed_dates:
            f.write("# Точний аналітичний дайджест новин\n")
            f.write(f"*Згенеровано за допомогою {args.model} (оцінка важливості, батчинг)*\n\n---\n\n")

        for date in sorted(daily_posts):
            summary = generate_daily_summary(session, date, daily_posts[date], args, args.channel)
            if summary is None:
                continue
            suffix = " (Оновлено)" if date == today_str and date in processed_dates else ""
            f.write(f"## 📅 Підсумок за {date}{suffix}\n\n{summary}\n\n---\n\n")
            f.flush()
            os.fsync(f.fileno())

    print(f"\nFinished. Digest file: '{args.output}'")
    if args.make_pdf:
        pdf_ok = convert_to_pdf(args.output, pdf_target(args, today_str))
        if args.delete_md:
            if pdf_ok:
                os.remove(args.output)
                print(f"Deleted '{args.output}' (the PDF is the final result).")
            else:
                print(f"PDF was not created, so '{args.output}' was kept.")


if __name__ == "__main__":
    main()