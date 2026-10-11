"""Model catalogue for Brattoi: what can be downloaded, what is installed, download and delete."""

import hashlib
import json
import os
import shutil
import socket
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOGUE = os.path.join(HERE, "models.json")
USER_AGENT = "Brattoi (MustardOS)"
CHUNK = 256 * 1024
SPARE_SPACE = 50 * 1024 * 1024


def catalogue():
    """Catalogue entries plus any other .gguf copied into the folder by hand."""
    with open(CATALOGUE, encoding="utf-8") as fh:
        entries = json.load(fh)
    known = {e["file"] for e in entries}
    for name in sorted(os.listdir(HERE)):
        if name.endswith(".gguf") and name not in known:
            entries.append({"file": name, "name": name[:-5], "rating": "Untested", "note": "Added by hand.",
                            "license": "Unknown"})
    return entries


def path(entry):
    return os.path.join(HERE, entry["file"])


def installed(entry):
    return os.path.isfile(path(entry))


def megabytes(n):
    return f"{n / 1e6:.0f} MB"


def online():
    try:
        socket.setdefaulttimeout(5)
        socket.getaddrinfo("huggingface.co", 443)
        return True
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(None)


def delete(entry):
    for p in (path(entry), path(entry) + ".part"):
        if os.path.exists(p):
            os.remove(p)


def verify(file_name, sha256):
    digest = hashlib.sha256()
    with open(file_name, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == sha256


def download(entry, progress, cancelled):
    """Download a catalogue model, resuming any partial file, then check its SHA-256.

    progress(done, total, bytes_per_second) is called as data arrives; cancelled() is polled
    between chunks. Returns "ok", "cancelled", "offline", "space", "bad" or "error: ...".
    """
    if not online():
        return "offline"

    part = path(entry) + ".part"
    total = entry["size"]
    done = os.path.getsize(part) if os.path.exists(part) else 0
    if shutil.disk_usage(HERE).free < total - done + SPARE_SPACE:
        return "space"

    if done < total:
        headers = {"User-Agent": USER_AGENT}
        if done:
            headers["Range"] = f"bytes={done}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(entry["url"], headers=headers), timeout=30) as resp:
                if done and resp.status != 206:
                    done = 0
                with open(part, "ab" if done else "wb") as fh:
                    started, start_done = time.time(), done
                    while True:
                        block = resp.read(CHUNK)
                        if not block:
                            break
                        fh.write(block)
                        done += len(block)
                        elapsed = max(time.time() - started, 0.001)
                        progress(done, total, (done - start_done) / elapsed)
                        if cancelled():
                            return "cancelled"
        except urllib.error.HTTPError as err:
            if err.code != 416:
                return f"error: HTTP {err.code}"
        except (OSError, urllib.error.URLError) as err:
            return f"error: {err}"

    progress(None, total, 0)
    if not verify(part, entry["sha256"]):
        os.remove(part)
        return "bad"
    os.replace(part, path(entry))
    return "ok"
