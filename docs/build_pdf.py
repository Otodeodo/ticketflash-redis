"""Genera docs/diseno.pdf a partir de docs/diseno.md.

Uso:  python docs/build_pdf.py
Requiere: pip install markdown   y Microsoft Edge o Chrome (modo headless).
Las fuentes (Anton, Inter) se cargan de Google Fonts; sin conexión se usan fuentes del sistema.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

import markdown

HERE = os.path.dirname(os.path.abspath(__file__))
MD = os.path.join(HERE, "diseno.md")
HTML = os.path.join(HERE, "diseno.html")
PDF = os.path.join(HERE, "diseno.pdf")

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link href="https://fonts.googleapis.com/css2?family=Anton&family=Inter:wght@400;500;600;700;800'
         '&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">')

CSS = """
:root { --ink:#0d0d10; --paper:#f5f4ef; --volt:#d8ff3c; --line:#e3e1d9; --muted:#74747f; --hot:#ff4d2e;
        --display:"Anton","Impact","Arial Narrow",sans-serif; --body:"Inter","Segoe UI",Arial,sans-serif;
        --mono:"JetBrains Mono",Consolas,Menlo,monospace; }
* { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
@page { size: A4; margin: 22mm 18mm 20mm;
        @bottom-left  { content: "TICKETFLASH · Diseño y justificación NoSQL"; font: 600 7.5pt "Inter", Arial; color: #74747f; letter-spacing: .6px; }
        @bottom-right { content: counter(page); font: 800 9pt "Inter", Arial; color: #0d0d10; } }
@page :first { margin: 0; @bottom-left { content: none; } @bottom-right { content: none; } }
html { font-size: 10.5pt; }
body { margin: 0; font-family: var(--body); line-height: 1.55; color: #1c1c22; }
p { margin: 0 0 8pt; text-align: left; }
b, strong { color: var(--ink); }
a { color: inherit; }

/* ------------------------------------------------------------- portada */
.cover { height: 296.5mm; background: var(--ink); color: #fff; padding: 20mm 18mm 16mm; display: flex; flex-direction: column;
         page-break-after: always; position: relative; overflow: hidden; }
.cover::after { content: ""; position: absolute; right: -60mm; top: -50mm; width: 190mm; height: 190mm; border-radius: 50%;
                background: radial-gradient(circle, rgba(216,255,60,.38), rgba(216,255,60,0) 66%); }
.cover > * { position: relative; z-index: 1; }
.cover-top { display: flex; justify-content: space-between; align-items: center; }
.cover .brand { font-family: var(--display); font-size: 20pt; letter-spacing: .5px; }
.cover .brand span { background: var(--volt); color: var(--ink); padding: 0 4px; margin-left: 3px; }
.cover .course { font-size: 8.5pt; letter-spacing: 2px; text-transform: uppercase; color: #9a9aa6; font-weight: 600; }
.cover-main { margin-top: auto; margin-bottom: auto; }
.kicker { text-transform: uppercase; letter-spacing: 3px; font-size: 8.5pt; font-weight: 700; color: var(--volt); margin: 0 0 10mm; }
.kicker.dark { color: var(--ink); background: var(--volt); display: inline-block; padding: 3px 10px; margin-bottom: 6mm; }
.cover .title { font-family: var(--display); font-weight: 400; font-size: 118pt; line-height: .86; margin: 0; letter-spacing: -1px; }
.cover .title mark { background: none; color: var(--volt); }
.cover .subtitle { font-size: 14pt; color: #c9c9d1; max-width: 125mm; margin: 10mm 0 0; line-height: 1.4; }
.cover-grid, .cover-foot { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: #2a2a31; border-radius: 3mm; overflow: hidden; }
.cover-grid { margin-bottom: 4mm; }
.cover-grid div { background: #15151a; padding: 4mm 4.5mm; }
.cover-foot { background: none; border-radius: 0; gap: 4mm; padding-top: 6mm; border-top: 1px solid #2a2a31; }
.cover small { display: block; font-size: 6.8pt; letter-spacing: 1.5px; text-transform: uppercase; color: #8a8a95; font-weight: 600; margin-bottom: 1mm; }
.cover b { color: #fff; font-size: 9pt; font-weight: 600; line-height: 1.3; display: block; word-break: break-word; }
.cover-grid b { font-size: 10.5pt; }

/* -------------------------------------------------------------- índice */
.toc { padding-top: 10mm; page-break-after: always; }
.toc-title { font-family: var(--display); font-weight: 400; font-size: 46pt; line-height: 1; text-transform: uppercase; margin: 0 0 10mm; border: 0; padding: 0; }
.toc ol { list-style: none; padding: 0; margin: 0; counter-reset: none; }
.toc li { display: flex; align-items: baseline; gap: 6mm; padding: 4.2mm 0; border-bottom: 1px solid var(--line); font-size: 13pt; font-weight: 600; }
.toc li span { font-family: var(--display); font-weight: 400; font-size: 24pt; color: transparent; -webkit-text-stroke: 1px var(--ink); width: 14mm; flex: none; line-height: 1; }

/* ------------------------------------------------------------ titulares */
h2 { display: flex; align-items: flex-end; gap: 5mm; font-family: var(--display); font-weight: 400; font-size: 25pt; line-height: 1.02; text-transform: uppercase;
     margin: 15mm 0 6mm; padding-bottom: 3mm; border-bottom: 3px solid var(--ink); page-break-after: avoid; }
h2 .num { background: var(--volt); color: var(--ink); padding: 1.5mm 3mm 0; font-size: 25pt; line-height: 1; flex: none; transform: rotate(-2deg); box-shadow: 1.2mm 1.2mm 0 var(--ink); }
h3 { font-size: 12pt; font-weight: 800; margin: 8mm 0 3mm; padding-left: 3.5mm; border-left: 4px solid var(--volt); color: var(--ink); page-break-after: avoid; line-height: 1.25; }
h2:first-of-type { margin-top: 0; }

/* ----------------------------------------------------------- contenido */
ul, ol { padding-left: 5mm; margin: 0 0 8pt; }
li { margin-bottom: 2.2pt; }
li::marker { color: var(--ink); font-weight: 800; }
code { font-family: var(--mono); font-size: 8.4pt; background: #ececd9; padding: 1px 4px; border-radius: 3px; color: #1a1a20; }
pre { background: var(--ink); color: #e7e7ee; border-radius: 3mm; padding: 4mm 5mm; margin: 4mm 0 6mm; font-size: 7.6pt; line-height: 1.5;
      white-space: pre-wrap; word-break: break-word; border-left: 4px solid var(--volt); page-break-inside: avoid; }
pre code { background: none; color: inherit; padding: 0; font-size: inherit; }
.tk-c { color: #7b7b8a; } .tk-k { color: #d8ff3c; } .tk-s { color: #9be7ff; }

table { border-collapse: separate; border-spacing: 0; width: 100%; margin: 4mm 0 7mm; font-size: 8.6pt; border: 1px solid var(--line); border-radius: 3mm; overflow: hidden; }
th { background: var(--ink); color: var(--volt); text-align: left; padding: 2.4mm 3mm; font-size: 7.6pt; letter-spacing: 1px; text-transform: uppercase; font-weight: 700; }
td { padding: 2.2mm 3mm; vertical-align: top; border-top: 1px solid var(--line); background: #fff; }
tbody tr:nth-child(even) td { background: #faf9f5; }
tr { page-break-inside: avoid; }
td:first-child { font-weight: 600; }
td code { font-size: 7.8pt; }

figure { margin: 6mm 0; page-break-inside: avoid; text-align: center; }
figure svg, figure img { width: 100%; height: auto; border-radius: 3mm; border: 1px solid var(--line); background: #fff; box-shadow: 0 1.2mm 0 var(--ink); }
figcaption { font-size: 8pt; color: var(--muted); margin-top: 3mm; text-align: left; padding-left: 1mm; font-weight: 500; }
figcaption::before { content: "▸ "; color: var(--ink); }

/* llamadas destacadas: párrafos que abren con una frase en negrita */
p > strong:first-child:not(:only-child) { background: linear-gradient(transparent 55%, rgba(216,255,60,.75) 55%); padding: 0 1px; }
blockquote { margin: 4mm 0; padding: 3mm 5mm; background: var(--paper); border-left: 4px solid var(--ink); border-radius: 0 3mm 3mm 0; }
"""


def decorate(body: str) -> str:
    """Numeración destacada en h2 y resaltado ligero de comentarios en bloques de código."""
    def h2(m: re.Match) -> str:
        return f'<h2><span class="num">{int(m.group(1)):02d}</span><span>{m.group(2)}</span></h2>'
    body = re.sub(r"<h2>(\d+)\.\s*(.*?)</h2>", h2, body)

    def code(m: re.Match) -> str:
        text = m.group(2)
        text = re.sub(r"(#[^\n]*|--[^\n]*)", r'<span class="tk-c">\1</span>', text)
        return f"{m.group(1)}{text}{m.group(3)}"
    return re.sub(r"(<pre><code[^>]*>)(.*?)(</code></pre>)", code, body, flags=re.S)


def build_html() -> str:
    with open(MD, encoding="utf-8") as fh:
        body = markdown.markdown(fh.read(), extensions=["tables", "fenced_code", "md_in_html"])
    html = (f'<!doctype html><html lang="es"><head><meta charset="utf-8">'
            f'<title>TicketFlash · Diseño y justificación NoSQL</title>{FONTS}<style>{CSS}</style></head>'
            f'<body>{decorate(body)}</body></html>')
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
           "--virtual-time-budget=15000", f"--print-to-pdf={PDF}", url]
    subprocess.run(cmd, check=True, timeout=180, capture_output=True)
    return PDF


if __name__ == "__main__":
    html = build_html()
    print("HTML:", html)
    pdf = build_pdf(html)
    print("PDF :", pdf, f"({os.path.getsize(pdf) // 1024} KB)")
