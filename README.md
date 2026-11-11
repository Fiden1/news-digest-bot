# Telegram AI News Digest

A Python tool that reads a public Telegram channel, lets a **local LLM** (via [Ollama](https://ollama.com/))
rate every post by importance, and writes a digest as Markdown and PDF. Each highlighted post keeps its
original text, photos and video thumbnails and gets a one-sentence AI analysis and a link to the original post.

## Features

- **No Telegram API key.** It reads the public web preview (`t.me/s/<channel>`).
- **Local and private.** Posts are analysed by a model running on your own machine through Ollama.
  Nothing is sent to a cloud AI service.
- **Importance rating.** The model rates posts from 1 to 5. Only posts at or above a threshold
  (default 4) end up in the digest.
- **Original text, never model text.** The model only returns post IDs, ratings and a short analysis.
  The quoted text always comes from the original post, so the model cannot change a quote.
- **Photos and video thumbnails** are downloaded for the selected posts and included in the digest.
- **PDF export** with the images embedded (uses headless Microsoft Edge or Google Chrome).
- **Resumable.** Days that are already in the digest file are skipped on the next run.

## Requirements

1. Python 3.12 or newer
2. [Ollama](https://ollama.com/) installed and running, with a model pulled, for example:
   `ollama pull <model name from config.toml>`
3. Microsoft Edge or Google Chrome (only needed for the PDF export; Edge is preinstalled on Windows)

## Installation

```bash
git clone <your-repo-url>
cd <your-repo-folder>

# with uv
uv sync

# or with pip
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install .
```

## Usage

Run the program from the project folder, because it reads `config.toml` from the current directory.

```bash
uv run main.py          # or: python main.py
```

Examples:

```bash
# start at a different day
python main.py --since 2026-11-01

# keep the Markdown file and skip the PDF
python main.py --no-make-pdf --no-delete-md

# only keep very important posts
python main.py --min-importance 5
```

## Configuration

Default values live in `config.toml`. Command-line options override them for a single run.

| Setting in `config.toml` | Command-line option | Meaning |
| :--- | :--- | :--- |
| `DEFAULT_CHANNEL` | `--channel` | Channel username without `@` |
| `DEFAULT_SINCE` | `--since` | First day to process (`YYYY-MM-DD`) |
| `DEFAULT_MODEL` | `--model` | Ollama model name |
| `DEFAULT_MIN_IMPORTANCE` | `--min-importance` | Minimum rating (1-5) for a post to be kept |
| `DEFAULT_OUTPUT` | `--output` | Markdown file that new days are appended to |
| `DEFAULT_IMAGES_DIR` | `--images-dir` | Folder for downloaded images |
| `DEFAULT_MAKE_PDF` | `--make-pdf` / `--no-make-pdf` | Create a PDF at the end |
| `DEFAULT_PDF` | `--pdf` | PDF file name (empty = derived from the Markdown name) |
| `DEFAULT_DELETE_MD` | `--delete-md` / `--no-delete-md` | Delete the Markdown file after the PDF was created |
| `DEFAULT_THINK` | `--think` / `--no-think` | Let reasoning models think before answering (slower) |

Settings without a command-line option: `BATCH_SIZE` (posts per LLM call), `MAX_EVENTS_PER_BATCH`,
`MAX_CHARS_PER_POST` (posts are shortened before they go to the model), `REQUEST_TIMEOUT`, `REQUEST_DELAY`.

## How it works

1. **Scrape.** The channel preview is read page by page from newest to oldest until the `--since` date
   or a day that is already in the digest is reached. Polls and channel boilerplate are removed.
2. **Rate.** The posts of each day are split into batches and sent to the local model.
   Its answer is constrained to a JSON schema (post ID, importance, analysis). Invalid IDs,
   duplicates and posts below the threshold are discarded.
3. **Download.** Images and video thumbnails are fetched only for the selected posts.
4. **Write.** The digest is written as Markdown. A day is only saved if all of its batches succeeded,
   so a failed day is retried on the next run.
5. **PDF.** The Markdown is converted to HTML and printed to PDF by headless Edge/Chrome.

## Project structure

| File | Task |
| :--- | :--- |
| `main.py` | Command-line options and the overall flow |
| `scraper.py` | Downloads the channel pages and turns them into `Post` objects |
| `cleaning.py` | Cleans post text |
| `models.py` | The `Post` data class |
| `llm.py` | Prompt, Ollama call and validation of the model's answer |
| `output.py` | Image download and Markdown rendering |
| `pdf.py` | Markdown to PDF conversion |
| `config.toml` | Default settings |

## Good to know

- With `DEFAULT_DELETE_MD = true`, the Markdown file is deleted after a successful PDF. The program then
  no longer knows which days are done and starts again at `--since` on the next run. Set it to `false`
  (or use `--no-delete-md`) if you want to continue where you stopped.
- Times in the digest are UTC. Telegram shows your local time.
- A small local model can misjudge importance or miss a post. The rating threshold makes this adjustable,
  but the result is not perfect, and posts that are not selected do not appear in the digest.
- The scraper depends on the HTML structure of `t.me/s/`, which Telegram can change.
- The texts and images belong to the channel. Do not publish generated digests or the `images/` folder.