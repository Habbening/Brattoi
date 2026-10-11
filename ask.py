#!/usr/bin/env python3
"""Look up muOS docs chunks for a question, optionally answer it with the LLM.

Usage:
  ask.py "question"            print the best matching chunks
  ask.py --answer "question"   feed the best chunk to the model and print its answer
"""

import json
import math
import os
import re
import subprocess
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(HERE, "docs.jsonl")
LLAMA = os.path.join(HERE, "llama-completion")
MODEL = os.path.join(HERE, os.environ.get("MUOS_LLM_MODEL", "gemma-3-270m-it-Q4_0.gguf"))

STOP = set(
    "a an and are as at be but by can do does for from how i if in is it its me my of on or "
    "so that the this to was what when where which who why will with you your".split()
)

# Everyday words mapped to the ones the muOS docs use.
SYNONYMS = {
    "folder": "directory",
    "folders": "directory",
    "emulator": "core",
    "emulators": "core",
    "sd": "msd",
    "microsd": "msd",
    "game": "content",
    "games": "content",
    "rom": "content",
    "roms": "content",
}

K1, B = 1.5, 0.75
TITLE_WEIGHT = 3
# Share of the question's idf weight a chunk must contain. Measured: real questions 0.54 and up,
# off-topic ones 0.46 and below.
MIN_COVERAGE = 0.5

HELP_SYSTEM = (
    "You are a helper for muOS, a firmware for handheld game consoles. "
    "Answer the question using only the notes from the muOS documentation, in two or three short "
    "sentences. If the notes do not answer it, say you are not sure and suggest muos.dev. "
    "Never mention shops, stores or buying games."
)

END_MARK = "[end of text]"


def tokens(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 1]


def load():
    docs = []
    with open(PACK, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            rec["terms"] = Counter(tokens(rec["text"]) + tokens(rec["title"]) * TITLE_WEIGHT)
            rec["length"] = sum(rec["terms"].values())
            docs.append(rec)
    return docs


def search(docs, question, top=2):
    query = set(tokens(question))
    query |= {SYNONYMS[t] for t in query if t in SYNONYMS}
    avg = sum(d["length"] for d in docs) / len(docs)
    df = Counter(t for d in docs for t in query if t in d["terms"])
    idf = {t: math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5)) for t in query}
    weight = sum(idf.values())

    scored = []
    for d in docs:
        score, covered = 0.0, 0.0
        for t in query:
            tf = d["terms"].get(t, 0)
            if not tf:
                continue
            covered += idf[t]
            score += idf[t] * tf * (K1 + 1) / (tf + K1 * (1 - B + B * d["length"] / avg))
        # Skip chunks that miss most of the question's distinctive words, so off-topic
        # questions ("Xbox 360", "a poem") find nothing rather than a forced match.
        if score > 0 and covered >= MIN_COVERAGE * weight:
            scored.append((score, d))

    scored.sort(key=lambda s: s[0], reverse=True)
    return scored[:top]


def generate(system, user, temp=0.3, max_tokens=120, out=None):
    """Run one chat turn with the model's own chat template.

    Streams the reply to `out` as it is generated when given, and returns the full reply.
    """
    proc = subprocess.Popen(
        [LLAMA, "-m", MODEL, "-t", "4", "-n", str(max_tokens), "--temp", str(temp),
         "-cnv", "-st", "--no-display-prompt", "-co", "off", "--simple-io", "--no-warmup",
         "-sys", system, "-p", user],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=0,
    )

    reply, held = "", ""
    while True:
        ch = proc.stdout.read(1)
        if not ch:
            break
        held += ch
        # Hold back anything that could be the start of the end marker.
        if END_MARK.startswith(held.strip()):
            continue
        reply += held
        if out:
            out.write(held)
            out.flush()
        held = ""
    proc.wait()
    return reply.strip()


class Chat:
    """A conversation with one llama.cpp process kept running between turns.

    llama.cpp's conversation mode keeps the history in the model's own chat template, and the
    model only loads once. It prints "> " when it is ready for the next message.
    """

    def __init__(self, system, temp=0.8, max_tokens=200):
        self.proc = subprocess.Popen(
            [LLAMA, "-m", MODEL, "-t", "4", "-c", "4096", "-n", str(max_tokens), "--temp", str(temp),
             "-cnv", "--no-display-prompt", "-co", "off", "--simple-io", "--no-warmup", "-sys", system],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=0,
        )
        self.alive = self._read(startup=True) is not None

    def _read(self, out=None, startup=False):
        """Read until the ready prompt, streaming everything before it. None if the process ended."""
        text, pending = "", ""
        while True:
            ch = self.proc.stdout.read(1)
            if not ch:
                return None
            pending += ch
            # The prompt is newlines then "> " (or just "> " before the first reply).
            lead = "\n*" if startup and not text else "\n+"
            if re.fullmatch(lead + "> ", pending):
                return text.strip()
            if re.fullmatch(lead + ">?", pending):
                continue
            if out:
                out.write(pending)
                out.flush()
            text += pending
            pending = ""

    def say(self, message, out=None):
        """Send one message and stream the reply. Returns the reply, or None if the model stopped."""
        if not self.alive:
            return None
        self.proc.stdin.write(message.replace("\n", " ") + "\n")
        self.proc.stdin.flush()
        reply = self._read(out)
        self.alive = reply is not None
        return reply

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def answer(question, chunks, out=None):
    notes = "\n".join(f"{i}. {c['text']}" for i, c in enumerate(chunks, 1))
    prompt = (
        f"Notes from the muOS documentation:\n{notes}\n\n"
        f"Question: {question}\nAnswer using only these notes."
    )
    return generate(HELP_SYSTEM, prompt, temp=0.2, max_tokens=150, out=out)


def main():
    args = sys.argv[1:]
    want_answer = bool(args) and args[0] == "--answer"
    if want_answer:
        args = args[1:]
    if not args:
        print(__doc__.strip())
        return 1

    question = " ".join(args)
    hits = search(load(), question)
    if not hits:
        print("No matching docs found.")
        return 0

    if want_answer:
        print(answer(question, [d for _, d in hits]))
        print(f"\nSource: {hits[0][1]['title']}\n{hits[0][1]['url']}")
    else:
        for score, d in hits:
            print(f"[{score:.1f}] {d['title']}\n  {d['url']}\n  {d['text'][:160]}...\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
