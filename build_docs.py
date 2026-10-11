#!/usr/bin/env python3
"""Build the muOS docs pack (docs.jsonl) for the on-device LLM helper.

Sources:
  - muos.dev: a local clone of MustardOS/mustardos.github.io (markdown pages
    and the per-module YAML under _data/modules)
  - community.muos.dev: every topic in the FAQ category, as raw markdown

Usage: build_docs.py <docs-site clone> <output dir>
"""

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

import yaml

FORUM = "https://community.muos.dev"
FAQ_CATEGORY = "/c/faq/7.json"
SITE = "https://muos.dev"
USER_AGENT = "muos-llm-docpack/0.1 (Habbening)"
CHUNK_WORDS = 150


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def clean(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\{[%{].*?[%}]\}", " ", text, flags=re.S)
    text = re.sub(r"[*_`>|#]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def chunks(title, url, body):
    """Split on headings, then cap each piece at CHUNK_WORDS."""
    out = []
    for section in re.split(r"\n(?=#{1,4} )", body):
        heading = re.match(r"#{1,4} (.+)", section)
        label = f"{title} - {clean(heading.group(1))}" if heading else title
        words = clean(section).split()
        for i in range(0, len(words), CHUNK_WORDS):
            piece = " ".join(words[i:i + CHUNK_WORDS])
            if len(piece) > 40:
                out.append({"title": label, "url": url, "text": piece})
    return out


def front_matter(text):
    match = re.match(r"---\n(.*?)\n---\n?(.*)", text, flags=re.S)
    if not match:
        return {}, text
    return yaml.safe_load(match.group(1)) or {}, match.group(2)


def site_pages(root):
    out = []
    for path in sorted(root.rglob("*.md")):
        if any(part.startswith((".", "_")) for part in path.relative_to(root).parts):
            continue
        meta, body = front_matter(path.read_text(encoding="utf-8"))
        if len(clean(body)) < 200:
            continue
        title = meta.get("title") or path.stem
        url = SITE + (meta.get("permalink") or "/" + path.relative_to(root).with_suffix("").as_posix())
        out += chunks(title, url, body)
    return out


def site_modules(root):
    pages = {}
    for path in sorted(root.rglob("tour/modules/*.md")):
        meta, _ = front_matter(path.read_text(encoding="utf-8"))
        if meta.get("module"):
            pages[meta["module"]] = meta

    out = []
    for path in sorted((root / "_data" / "modules").glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        meta = pages.get(path.stem, {})
        title = meta.get("title") or path.stem
        url = SITE + (meta.get("permalink") or f"/tour/modules/{path.stem}")
        if data.get("description"):
            out.append({"title": f"{title} menu", "url": url, "text": clean(data["description"])})
        for group in ("visible", "hidden"):
            for item in data.get(group) or []:
                if item.get("name") and item.get("desc"):
                    out.append({
                        "title": f"{title} menu - {item['name']}",
                        "url": url,
                        "text": f"{item['name']}: {clean(item['desc'])}",
                    })
    return out


def forum_faq():
    topics, page = [], FAQ_CATEGORY
    while page:
        listing = json.loads(fetch(FORUM + page))["topic_list"]
        topics += listing["topics"]
        more = listing.get("more_topics_url")
        page = more.replace("/c/faq/7", "/c/faq/7.json") if more else None
        time.sleep(1)

    out = []
    for topic in topics:
        raw = fetch(f"{FORUM}/raw/{topic['id']}")
        raw = re.sub(r"\A.*?\| #1\s*", "", raw, count=1, flags=re.S)
        out += chunks(topic["title"], f"{FORUM}/t/{topic['slug']}/{topic['id']}", raw)
        time.sleep(1)
    return out, len(topics)


def main():
    site, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    pages = site_pages(site)
    modules = site_modules(site)
    faq, faq_topics = forum_faq()

    records = faq + modules + pages
    with open(out_dir / "docs.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"forum FAQ: {faq_topics} topics, {len(faq)} chunks")
    print(f"site modules: {len(modules)} chunks")
    print(f"site pages: {len(pages)} chunks")
    print(f"total: {len(records)} chunks -> {out_dir / 'docs.jsonl'}")


if __name__ == "__main__":
    main()
