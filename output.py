import requests
from urllib.parse import urlparse
from pathlib import Path
import tomllib
import os
from llm import chunk_list , pick_events

with open("config.toml", "rb") as f:
    config = tomllib.load(f)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

def download_image(session, url, images_dir, stem):
    """Save one image and return its path (None if the download failed)."""
    extension = Path(urlparse(url).path).suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        extension = ".jpg"
    path = images_dir / f"{stem}{extension}"
    if path.exists():
        return path
    try:
        response = session.get(url, timeout=config["start"]["REQUEST_TIMEOUT"])
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
    batches = list(chunk_list(posts, config["start"]["BATCH_SIZE"]))
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

