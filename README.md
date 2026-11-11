# Telegram AI News Digest

An automated Python tool that scrapes public Telegram channels, uses a local Large Language Model (via Ollama) to analyze and rate the strategic importance of each post, and generates a formatted Markdown and PDF digest. 

The digest beautifully mimics the Telegram interface, keeping the original text, photos, and video thumbnails, while appending a short, AI-generated analysis.

## 🚀 Features
* **No API Keys Required:** Scrapes the public Telegram web preview (`t.me/s/...`).
* **Privacy First:** Relies on [Ollama](https://ollama.com/) for local LLM inference. No data is sent to OpenAI, Google, or any other cloud provider.
* **Smart Filtering:** The AI rates posts on a 1-5 scale and filters out minor news, opinions, and ads, keeping only the most important macro-events.
* **PDF Generation:** Automatically compiles the Markdown and downloaded images into a clean, readable PDF using headless Microsoft Edge or Google Chrome.

## 📋 Prerequisites
1. **Python 3.12+**
2. **Ollama:** Installed and running locally. You will need to pull your preferred model (e.g., `ollama run gemma2`).
3. **Browser:** Microsoft Edge or Google Chrome (required for the automatic PDF conversion).

## 🛠️ Installation

Because this project uses a `pyproject.toml`, you can install it easily using pip or your favorite package manager (like `uv` or `poetry`).

```bash
# Clone the repository
git clone <your-repo-url>
cd <your-repo-folder>

# Create a virtual environment and activate it
python -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate

# Install the project and its dependencies
pip install .

# Also install markdown for the PDF conversion feature
pip install markdown
```

## 💻 Usage

Run the script from the command line. By default, it looks at the `znua_live` channel.

```bash
python main.py
```

### Command Line Arguments

You can heavily customize the tool's behavior using CLI flags:

| Argument | Default | Description |
| :--- | :--- | :--- |
| `--channel` | `znua_live` | The Telegram channel username (without the `@`). |
| `--since` | `2026-07-09` | The earliest date to process in `YYYY-MM-DD` format. |
| `--model` | `gemma4:e4b` | The local Ollama model to use for analysis. |
| `--min-importance`| `4` | Minimum AI rating (1-5) to include a post in the digest. |
| `--output` | `news_digest_ongoing.md` | The name of the temporary/appended Markdown file. |
| `--images-dir` | `images` | Folder where scraped thumbnails and photos are saved. |
| `--[no-]make-pdf` | `--make-pdf` | Whether to generate a PDF file at the end. |
| `--[no-]think` | `--think` | Allows "reasoning" models to think before answering. |
| `--[no-]delete-md`| `--delete-md` | Deletes the Markdown file after a successful PDF creation. |

### Examples

**Analyze a different channel with a different model:**
```bash
python main.py --channel kyivoperativ --model llama3 --since 2026-10-01
```

**Keep the markdown file and skip PDF generation:**
```bash
python main.py --no-make-pdf --no-delete-md
```

## 🏗️ How it Works
1. **Scraping:** Connects to `t.me/s/<channel>` and walks backward chronologically until it hits the `--since` date or a date it has already processed.
2. **Batching & AI:** Chunks posts into batches of 15 and sends them to the local Ollama model. The model strictly returns JSON containing the post IDs that matter, a 1-5 rating, and a 1-sentence analysis.
3. **Downloading:** Fetches the images/video thumbnails for the approved posts.
4. **Rendering:** Stitches the text, images, and AI analysis together into Markdown, then calls headless Chrome/Edge to "print" it to a stylized PDF.