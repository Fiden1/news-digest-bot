"""Telegram channel digest.

Scrapes the public web preview of a Telegram channel (t.me/s/<channel>), lets a
local LLM (Ollama) rate the posts by importance and writes a Markdown digest.
Every highlighted post is shown like in Telegram: photo / video thumbnail first,
then the original text, plus a short analysis and a link to the original post.
The digest is written as Markdown and also converted to a PDF with the images embedded.

Extra package for the PDF:  pip install markdown   (uses Microsoft Edge / Chrome to print the PDF)
"""

import argparse
import os
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from scraper import make_session ,fetch_posts
from pdf import convert_to_pdf
from output import generate_daily_summary

with open("config.toml", "rb") as f:
    config = tomllib.load(f)



def parse_args():
    parser = argparse.ArgumentParser(description="AI digest of a public Telegram channel.")
    parser.add_argument("--channel", default=config["start"]["DEFAULT_CHANNEL"], help="channel username without @")
    parser.add_argument("--since", default=config["start"]["DEFAULT_SINCE"], help="first day to process, YYYY-MM-DD")
    parser.add_argument("--model", default=config["start"]["DEFAULT_MODEL"], help="Ollama model name")
    parser.add_argument("--min-importance", type=int, default=config["output"]["DEFAULT_MIN_IMPORTANCE"], choices=range(1, 6))
    parser.add_argument("--output", default=config["output"]["DEFAULT_OUTPUT"], help="Markdown file (appended to)")
    parser.add_argument("--images-dir", default=config["start"]["DEFAULT_IMAGES_DIR"], help="folder for downloaded images")
    parser.add_argument("--pdf", default=config["output"]["DEFAULT_PDF"], help="PDF file (default: same name as --output, .pdf)")
    parser.add_argument("--make-pdf", dest="make_pdf", action="store_true", help="create a PDF")
    parser.add_argument("--no-pdf", dest="make_pdf", action="store_false", help="do not create a PDF")
    parser.add_argument("--think", action=argparse.BooleanOptionalAction, default=config["start"]["DEFAULT_THINK"],
                        help="let the model reason before answering (--no-think to switch off)")
    parser.add_argument("--delete-md", action=argparse.BooleanOptionalAction, default=config["output"]["DEFAULT_DELETE_MD"],
                        help="delete the Markdown file after the PDF was created (--no-delete-md to keep it)")
    parser.set_defaults(make_pdf=config["output"]["DEFAULT_MAKE_PDF"])
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

    needs_header = not os.path.exists(args.output) or os.path.getsize(args.output) == 0
    
    with open(args.output, "a", encoding="utf-8") as f:
        for date in sorted(daily_posts):
            summary = generate_daily_summary(session, date, daily_posts[date], args, args.channel)
            if summary is None:
                continue
            if needs_header:                      # only once, right before the first finished day
                f.write("# Точний аналітичний дайджест новин\n")
                f.write(f"*Згенеровано за допомогою {args.model} (оцінка важливості, батчинг)*\n\n---\n\n")
                needs_header = False
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