import shutil
import subprocess
import tempfile
import os
from pathlib import Path

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
