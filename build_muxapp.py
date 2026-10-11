#!/usr/bin/env python3
"""Package Brattoi as a .muxapp for the muOS Archive Manager. Models are not included.

Usage: build_muxapp.py <llama-completion binary> <version>
Writes dist/Brattoi_v<version>.muxapp
"""

import os
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
STAMP = time.localtime()[:6]
APP = "Brattoi"

# (source, path inside the archive, mode)
FILES = [
    ("app/Brattoi/mux_launch.sh", "mux_launch.sh", 0o755),
    ("app/Brattoi/glyph/brattoi.svg", "glyph/brattoi.svg", 0o644),
    ("app/Brattoi/mux_option.gov", "mux_option.gov", 0o644),
    ("art.py", ".brattoi/art.py", 0o644),
    ("ask.py", ".brattoi/ask.py", 0o644),
    ("chat.py", ".brattoi/chat.py", 0o644),
    ("models.py", ".brattoi/models.py", 0o644),
    ("models.json", ".brattoi/models.json", 0o644),
    ("pack/docs.jsonl", ".brattoi/docs.jsonl", 0o644),
    ("LICENSE", ".brattoi/LICENSE", 0o644),
    ("THIRD_PARTY.md", ".brattoi/THIRD_PARTY.md", 0o644),
]


def add_dir(zf, name):
    info = zipfile.ZipInfo(name.rstrip("/") + "/", STAMP)
    info.create_system = 3
    info.external_attr = (0o40755 << 16) | 0x10
    zf.writestr(info, b"")


def add_file(zf, src, name, mode):
    info = zipfile.ZipInfo(name, STAMP)
    info.create_system = 3
    info.external_attr = (0o100000 | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    with open(src, "rb") as fh:
        zf.writestr(info, fh.read())


def main():
    llama, version = sys.argv[1], sys.argv[2]
    files = [(os.path.join(HERE, s), n, m) for s, n, m in FILES] + [(llama, ".brattoi/llama-completion", 0o755)]

    out_dir = os.path.join(HERE, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{APP}_v{version}.muxapp")

    with zipfile.ZipFile(out, "w") as zf:
        for d in (APP, f"{APP}/glyph", f"{APP}/.brattoi"):
            add_dir(zf, d)
        for src, name, mode in files:
            add_file(zf, src, f"{APP}/{name}", mode)

    print(f"{out}: {os.path.getsize(out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
