# -*- coding: utf-8 -*-
"""build_exe.py  PyInstaller onefile console builder."""
import PyInstaller.__main__
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))

BILI_LIB_PATH = os.environ.get("BILI_LIB")
if not BILI_LIB_PATH:
    candidates = []
    for p in sys.path:
        if os.path.isdir(p) and os.path.basename(p) in ("site-packages", "dist-packages"):
            candidates.append(p)
    if not candidates:
        sys.exit("[!] Cannot locate site-packages, set env BILI_LIB=<dir containing bilibili_api>")
    BILI_LIB_PATH = candidates[0]
    print("[*] Using site-packages:", BILI_LIB_PATH)

bili_pkg = os.path.join(BILI_LIB_PATH, "bilibili_api")
if not os.path.isdir(bili_pkg):
    sys.exit("[!] bilibili_api package not found at: " + bili_pkg)

data_args = []
for src, dst in [(bili_pkg, "bilibili_api")]:
    data_args += ["--add-data", f"{src}{os.pathsep}{dst}"]

hiddens = [
    "bilibili_api",
    "bilibili_api.utils.credential",
    "bilibili_api.utils.network",
    "bilibili_api.utils.parse",
    "bilibili_api.utils.sync",
    "bilibili_api.utils.short",
    "bilibili_api.utils.Danmaku",
    "bilibili_api.video_upload",
    "bilibili_api.topic",
    "bilibili_api.cheese",
    "bilibili_api.cheese.grpc",
    "bilibili_api.cheese.utils",
    "bilibili_api.cheese.exceptions",
    "bilibili_api.errors",
    "aiohttp",
    "tqdm",
]

PyInstaller.__main__.run([
    os.path.join(HERE, "bdownloader_3.0.py"),
    "--name", "bilibili-cheese-downloader",
    "--onefile",
    "--console",
    "--clean",
    "--noconfirm",
    *[x for h in hiddens for x in ("--hidden-import", h)],
    *data_args,
])
