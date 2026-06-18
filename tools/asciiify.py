#!/usr/bin/env python3
"""Convert app source to pure ASCII so files render correctly in ANY editor encoding.

The files are valid UTF-8 and render fine in the browser, but an editor that opens them as
Windows-1252 shows mojibake. This rewrites:
  * prose symbols (em dash, arrows, <=, x, ... )  -> ASCII equivalents (every file type)
  * UI icon glyphs (play/pause/gear/pencil/check)  -> source escapes that render IDENTICALLY
      .js/.py -> \\uXXXX   |   .html/.md -> &#DDDD;
No CSS file contains non-ASCII, so no CSS escaping is needed.

Idempotent: escapes/ASCII are themselves ASCII, so re-running is a no-op.

    python tools/asciiify.py            # convert in place
    python tools/asciiify.py --dry      # report only
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXTS = (".py", ".js", ".html", ".css", ".md", ".txt")
SKIP_DIRS = {"node_modules", "vendor", ".venv", "maps3d", "cache", "nades", ".git",
             "__pycache__", "maps"}
SKIP_FILES = {"Claude_CS2DemoPlayer_Upgrade_Prompt.txt", "asciiify.py", "scan_nonascii.py"}

# Prose / math symbols -> ASCII (applied to every file type).
PROSE = {
    "—": "--",   # em dash
    "–": "-",    # en dash
    "−": "-",    # minus sign
    "·": "|",    # middle dot (used as a UI separator)
    "•": "-",    # bullet
    "→": "->",   # right arrow
    "←": "<-",   # left arrow
    "⟹": "=>",   # long right double arrow
    "⟸": "<=",   # long left double arrow
    "×": "x",    # multiplication sign
    "✕": "x",    # multiplication x
    "≤": "<=",   # <=
    "≥": ">=",   # >=
    "≈": "~",    # almost equal
    "…": "...",  # ellipsis
    "°": " deg", # degree
    "§": "Sec ", # section sign
    "Σ": "Sum",  # Greek capital sigma
    "＋": "+",    # fullwidth plus
    "∈": " in ", # element of
    "±": "+/-",  # plus-minus
    "◇": "*",    # white diamond
    "⊙": "*",    # circled dot operator
    "ü": "u",    # u-umlaut (transliterate)
}

# UI icon glyphs -> preserved via source escapes (render identically).
ICONS = ["▶", "⏸", "⚙", "✎", "✓", "✅", "●"]


def icon_repl(ext, ch):
    cp = ord(ch)
    if ext in (".html", ".md"):
        return f"&#{cp};"
    return "\\u%04x" % cp   # .js, .py, .css, .txt


def convert(text, ext):
    n = 0
    for ch, rep in PROSE.items():
        if ch in text:
            n += text.count(ch)
            text = text.replace(ch, rep)
    for ch in ICONS:
        if ch in text:
            n += text.count(ch)
            text = text.replace(ch, icon_repl(ext, ch))
    return text, n


def main():
    dry = "--dry" in sys.argv
    total = 0
    for p in sorted(REPO.rglob("*")):
        if not p.is_file() or p.suffix not in EXTS or p.name in SKIP_FILES:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        new, n = convert(text, p.suffix)
        if n:
            total += n
            rel = p.relative_to(REPO)
            print(f"  {n:4d}  {rel}")
            if not dry:
                p.write_text(new, encoding="utf-8")
    # report any non-ASCII still present (should be none after a real run)
    if not dry:
        left = {}
        for p in sorted(REPO.rglob("*")):
            if not p.is_file() or p.suffix not in EXTS or p.name in SKIP_FILES:
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            try:
                t = p.read_text(encoding="utf-8")
            except Exception:
                continue
            na = sum(1 for c in t if ord(c) > 127)
            if na:
                left[str(p.relative_to(REPO))] = na
        print(f"\nconverted {total} chars across files; remaining non-ASCII: {left or 'NONE'}")
    else:
        print(f"\n[dry] {total} chars would be converted")


if __name__ == "__main__":
    main()
