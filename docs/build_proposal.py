"""Render the proposal markdown into a standalone HTML file and a PDF.

Usage: python docs/build_proposal.py   (needs markdown-it-py and google-chrome)
"""
import subprocess
import time
from pathlib import Path

from markdown_it import MarkdownIt

DOCS = Path(__file__).parent
SRC = DOCS / "CSAFI-Reels-Library-Bot-Proposal.md"
HTML = SRC.with_suffix(".html")
PDF = SRC.with_suffix(".pdf")

CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       color: #1f2328; line-height: 1.55; font-size: 10.5pt; max-width: 860px; margin: 32px auto; padding: 0 20px; }
h1 { font-size: 22pt; margin: 0 0 4px; }
h2 { font-size: 15pt; margin: 28px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #d0d7de; }
h3 { font-size: 11.5pt; margin: 18px 0 6px; }
p, li { margin: 4px 0; }
em { color: #59636e; }
table { border-collapse: collapse; width: 100%; margin: 10px 0 14px; font-size: 9.5pt; page-break-inside: avoid; }
th, td { border: 1px solid #d0d7de; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; font-weight: 600; }
code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 9pt; background: #f6f8fa; padding: 1px 4px; border-radius: 4px; }
pre { background: #f6f8fa; padding: 10px 12px; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; }
pre code { background: none; padding: 0; }
blockquote { margin: 12px 0; padding: 8px 14px; border-left: 3px solid #0969da; background: #f3f8ff; color: #1f2328; }
hr { border: none; border-top: 1px solid #d0d7de; margin: 20px 0; }
h3 + p { margin-top: 2px; }
"""


def main() -> None:
    body = MarkdownIt("commonmark").enable("table").render(SRC.read_text(encoding="utf-8"))
    HTML.write_text(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>CSAFI Reels Library Bot - Project Proposal for Review</title>"
        f"<style>{CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )
    PDF.unlink(missing_ok=True)
    chrome = subprocess.Popen(
        [
            "google-chrome", "--headless=new", "--no-sandbox", "--disable-gpu",
            "--no-pdf-header-footer", f"--print-to-pdf={PDF}", HTML.resolve().as_uri(),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Headless Chrome can keep running after the PDF is written, so stop it ourselves.
    for _ in range(120):
        if chrome.poll() is not None or (PDF.exists() and PDF.stat().st_size > 0):
            break
        time.sleep(0.5)
    time.sleep(1)
    chrome.kill()
    if not PDF.exists():
        raise SystemExit("PDF was not created")
    print(f"Wrote {HTML}\nWrote {PDF}")


if __name__ == "__main__":
    main()
