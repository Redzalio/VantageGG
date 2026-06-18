#!/usr/bin/env python3
"""Inventory non-ASCII characters across app source (to plan an ASCII conversion)."""
import collections
import os

EXTS = (".py", ".js", ".html", ".css", ".md")
SKIP_DIRS = {"node_modules", "vendor", ".venv", "maps3d", "cache", "nades", ".git", "__pycache__"}
SKIP_FILES = {"Claude_CS2DemoPlayer_Upgrade_Prompt.txt"}

perchar = collections.Counter()
perfile = collections.Counter()
for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    if "maps" in root.split(os.sep):   # static/maps radar images dir name clash guard
        pass
    for fn in files:
        if not fn.endswith(EXTS) or fn in SKIP_FILES:
            continue
        p = os.path.join(root, fn)
        try:
            t = open(p, encoding="utf-8").read()
        except Exception:
            continue
        na = [c for c in t if ord(c) > 127]
        if na:
            perfile[p] = len(na)
            for c in na:
                perchar[c] += 1

import unicodedata
print("FILES with non-ASCII:")
for f, n in perfile.most_common():
    print(f"  {n:4d}  {f}")
print("\nCHARS:")
for c, n in perchar.most_common():
    try:
        name = unicodedata.name(c)
    except Exception:
        name = "<no name>"
    print(f"  {n:4d}  U+{ord(c):04X}  {name}")
