#!/usr/bin/env python3
"""Generate the FN page (fn/index.html) from the shared index.html.

Both leagues use the same page; FN just gets the "trike" skin, its own title and theme color.
Its icons live in fn/ (icon.svg, icon-192.png, icon-512.png, apple-touch-icon.png, manifest.webmanifest).
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "index.html")).read()
for a, b in [('<html lang="en" data-theme="dark">', '<html lang="en" data-theme="dark" data-skin="trike">'),
             ('<meta name="theme-color" content="#0F1512">', '<meta name="theme-color" content="#161112">'),
             ('<meta name="apple-mobile-web-app-title" content="DynoView">', '<meta name="apple-mobile-web-app-title" content="DynoView FN">'),
             ("<title>DynoView</title>", "<title>DynoView FN</title>")]:
    assert a in src, a
    src = src.replace(a, b, 1)
os.makedirs(os.path.join(HERE, "fn"), exist_ok=True)
open(os.path.join(HERE, "fn", "index.html"), "w").write(src)
print("wrote fn/index.html")

# DynoDraft page: the template plus the two mascots from the main page (one source of truth for the drawings)
def mascot(fn):
    return src0.split(f"function {fn}(){{\n  return `")[1].split("`;")[0]
src0 = open(os.path.join(HERE, "index.html")).read()
tpl = open(os.path.join(HERE, "draft", "page.template.html")).read()
tpl = tpl.replace("/*REX*/", mascot("rex")).replace("/*TRIKE*/", mascot("trike"))
open(os.path.join(HERE, "draft", "index.html"), "w").write(tpl)
print("wrote draft/index.html")
