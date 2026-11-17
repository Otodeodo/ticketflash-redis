"""Genera docs/diseno.pdf a partir de docs/diseno.md.

Uso:  python docs/build_pdf.py
Requiere: pip install markdown   y Microsoft Edge (modo headless) o Chrome.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import markdown

HERE = os.path.dirname(os.path.abspath(__file__))
MD = os.path.join(HERE, "diseno.md")
HTML = os.path.join(HERE, "diseno.html")
PDF = os.path.join(HERE, "diseno.pdf")

CSS = """
@page { size: A4; margin: 20mm 18mm; }
body { font-family: "Segoe UI", Calibri, Arial, sans-serif; font-size: 11pt; line-height: 1.45;
       color: #1f2937; max-width: 100%; }
h1, h2, h3 { color: #111827; page-break-after: avoid; }
h2 { font-size: 16pt; margin-top: 26pt; border-bottom: 2px solid #4f46e5; padding-bottom: 4pt; }
h3 { font-size: 12.5pt; margin-top: 16pt; color: #312e81; }
p { text-align: justify; }
code { font-family: Consolas, Menlo, monospace; font-size: 9.5pt; background: #f3f4f6;
       padding: 1px 3px; border-radius: 3px; }
pre { background: #f8fafc; border: 1px solid #e5e7eb; border-left: 4px solid #4f46e5;
      padding: 8pt 10pt; font-size: 9pt; line-height: 1.35; white-space: pre-wrap;
      page-break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 9pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0 12pt; font-size: 9.5pt;
        page-break-inside: auto; }
th, td { border: 1px solid #d1d5db; padding: 4pt 6pt; vertical-align: top; text-align: left; }
th { background: #eef2ff; color: #312e81; }
tr { page-break-inside: avoid; }
figure { margin: 12pt 0; text-align: center; page-break-inside: avoid; }
figure svg { width: 100%; height: auto; border: 1px solid #e5e7eb; border-radius: 6px; }
figure img { width: 100%; height: auto; border: 1px solid #e5e7eb; border-radius: 6px; }
figcaption { font-size: 9.5pt; color: #6b7280; margin-top: 4pt; font-style: italic; }
ul, ol { padding-left: 20pt; }
li { margin-bottom: 3pt; }
.cover { height: 240mm; display: flex; flex-direction: column; justify-content: center;
         page-break-after: always; }
.cover .kicker { color: #4f46e5; font-weight: 600; letter-spacing: .5px; text-transform: uppercase;
                 font-size: 10pt; text-align: left; }
.cover .title { font-size: 40pt; margin: 6pt 0; color: #111827; }
.cover .subtitle { font-size: 15pt; color: #374151; text-align: left; margin-bottom: 30pt; }
.cover .meta { width: auto; font-size: 11pt; }
.cover .meta td:first-child { font-weight: 600; background: #eef2ff; width: 150px; }
.toc { page-break-after: always; }
.toc ol { font-size: 12pt; line-height: 1.9; }
"""


def build_html() -> str:
    with open(MD, encoding="utf-8") as fh:
        body = markdown.markdown(fh.read(), extensions=["tables", "fenced_code", "md_in_html"])
    html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>TicketFlash - Diseño NoSQL</title><style>{CSS}</style></head><body>{body}</body></html>"""
    with open(HTML, "w", encoding="utf-8") as fh:
        fh.write(html)
    return HTML


def find_browser() -> str | None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")


def build_pdf(html_path: str) -> str:
    browser = find_browser()
    if not browser:
        sys.exit("No se encontró Edge/Chrome. Abre docs/diseno.html y usa 'Imprimir > Guardar como PDF'.")
    url = "file:///" + html_path.replace("\\", "/")
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
           f"--print-to-pdf={PDF}", url]
    subprocess.run(cmd, check=True, timeout=120, capture_output=True)
    return PDF


if __name__ == "__main__":
    html = build_html()
    print("HTML:", html)
    pdf = build_pdf(html)
    print("PDF :", pdf, f"({os.path.getsize(pdf) // 1024} KB)")
