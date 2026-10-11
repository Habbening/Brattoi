#!/usr/bin/env python3
"""Brattoi: a tiny on-device LLM menu for Mustard Terminal (muxterm).

D-pad up/down moves, A picks, B goes back. Typed questions use the on-screen keyboard (Select
shows it, Start is Enter) or a USB keyboard. Menu quits the terminal.
"""

import glob
import itertools
import os
import re
import select
import shutil
import struct
import sys
import termios
import textwrap
import threading
import time

import art
import ask
import models

HELP_QUESTIONS = [
    "Why didn't my game save?",
    "Why is my battery percentage wrong?",
    "How do I assign a core to a folder?",
    "What CPU governor should I use?",
    "Where do I put BIOS files?",
    "What is the difference between Pickles and RetroArch?",
    "Should I use a swapfile or ZRAM?",
    "How do I add more games?",
]

FUN_SYSTEM = "You are Brattoi, a cheeky potato who lives inside a retro handheld game console. Keep it short."

FUN_PROMPTS = [
    "Write a haiku about Donkey Kong.",
    "Roast my game backlog.",
    "Invent a new Mario power-up and describe it.",
    "Tell me a joke about save states.",
    "Describe the perfect handheld in one sentence.",
    "Introduce yourself, Brattoi.",
    "Give me a pep talk before a hard boss fight.",
]

CHAT_SYSTEM = "You are Brattoi, a friendly potato who lives inside a retro handheld game console. Keep answers short."

# Leave the last column free so a full-width line never wraps on its own.
WIDTH = max(30, shutil.get_terminal_size((39, 20)).columns - 1)
RULE = "-" * WIDTH

BOLD, DIM, RESET = "\x1b[1m", "\x1b[2m", "\x1b[0m"
MUSTARD, CYAN = "\x1b[33m", "\x1b[36m"

MODEL_SETTING = os.path.join(ask.HERE, "model.txt")
CONTROLS_SEEN = os.path.join(ask.HERE, "controls_seen")
# When pack/docs.jsonl was built; muOS changes, so help answers say how old their notes are.
DOCS_DATE = "October 2026"

# Fits 38 columns; testers missed the keyboard and scrollback buttons when only the wrapped hints showed.
CONTROLS = [
    ("Menus", None),
    ("Up/Down", "Move"),
    ("A", "Select"),
    ("B", "Back"),
    ("Typing", None),
    ("Select", "Show keyboard"),
    ("", "(again: move it, hide it)"),
    ("D-pad + A", "Type a key"),
    ("X or Start", "Send"),
    ("Send empty", "End chat, go back"),
    ("Other", None),
    ("L2 / R2", "Scroll the text"),
    ("Menu", "Quit Brattoi"),
]
TYPE_HINT = "X/Start=Send  Empty=End  Select=Keys"

WARNING = (
    "Brattoi is a very small AI model doing its best. It will make mistakes and say silly "
    "things. It is for fun, a demo of what this hardware and MustardOS can do, and should not "
    "be taken too seriously. For actual help, visit muos.dev."
)


def say(text=""):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def clear():
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


class Pad:
    """Read the d-pad and face buttons from muinput's virtual "muOS-Keys" device.

    Mustard Terminal keeps its own controls, but it does not grab the device, so we can read
    the same events alongside it.
    """

    EVENT = struct.Struct("llHHi")
    EV_KEY, EV_ABS = 1, 3
    BUTTONS = {304: "a", 305: "b", 307: "x"}
    ABS_HAT0Y = 17

    def __init__(self):
        self.fd = None
        if os.environ.get("BRATTOI_NO_PAD"):
            return
        for name_file in glob.glob("/sys/class/input/event*/device/name"):
            with open(name_file) as fh:
                if fh.read().strip() == "muOS-Keys":
                    node = "/dev/input/" + name_file.split("/")[4]
                    self.fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
                    break

    def drain(self):
        """Drop presses made while the model was busy."""
        if self.fd is None:
            return
        try:
            while os.read(self.fd, self.EVENT.size * 64):
                pass
        except BlockingIOError:
            pass

    def keys(self):
        """Return the presses waiting right now: up, down, a, b or x."""
        try:
            data = os.read(self.fd, self.EVENT.size * 64)
        except BlockingIOError:
            return []
        found = []
        for _, _, kind, code, value in self.EVENT.iter_unpack(data):
            if kind == self.EV_ABS and code == self.ABS_HAT0Y and value:
                found.append("up" if value < 0 else "down")
            elif kind == self.EV_KEY and value == 1 and code in self.BUTTONS:
                found.append(self.BUTTONS[code])
        return found

    def press(self):
        """Block until a mapped button is pressed and return it."""
        while True:
            select.select([self.fd], [], [])
            found = self.keys()
            if found:
                return found[0]


class AnsiOut:
    """Stream writer that turns the model's markdown into ANSI styling for Mustard Terminal.

    Handles **bold**, *italic*, `code`, # headings and - or * bullets. A star or dash is held
    back until the next character shows what it means.
    """

    def __init__(self):
        self.bold = self.italic = self.code = self.heading = False
        self.line_start = True
        self.held = ""

    def write(self, text):
        for ch in text:
            self.feed(ch)
        sys.stdout.flush()

    def flush(self):
        sys.stdout.flush()

    def feed(self, ch):
        held, self.held = self.held, ""
        if held == "*":
            if ch == "*":
                self.bold = not self.bold
                sys.stdout.write(BOLD if self.bold else "\x1b[22m")
                return
            if self.line_start and ch == " ":
                return self.bullet()
            if ch == " " and not self.italic:
                sys.stdout.write("* ")
                return
            self.italic = not self.italic
            sys.stdout.write("\x1b[3m" if self.italic else "\x1b[23m")
        elif held == "-":
            if ch == " ":
                return self.bullet()
            self.line_start = False
            sys.stdout.write("-")

        if self.code:
            if ch == "`":
                self.code = False
                sys.stdout.write("\x1b[39m")
            else:
                sys.stdout.write(ch)
            return

        if ch == "*" or (ch == "-" and self.line_start):
            self.held = ch
            return
        if ch == "`":
            self.code = True
            sys.stdout.write(MUSTARD)
            return
        if ch == "\n":
            if self.heading:
                self.heading = False
                sys.stdout.write(RESET)
            self.line_start = True
            sys.stdout.write(ch)
            return
        if self.line_start:
            if ch == "#":
                self.heading = True
                sys.stdout.write(BOLD + CYAN)
                return
            if self.heading and ch == " ":
                return
            self.line_start = False
        sys.stdout.write(ch)

    def bullet(self):
        self.line_start = False
        sys.stdout.write(f"  {MUSTARD}•{RESET} ")

    def finish(self):
        if self.held:
            sys.stdout.write(self.held)
        sys.stdout.write(RESET)
        sys.stdout.flush()


PAD = Pad()


def quiet_input():
    """Stop the terminal echoing input, and hand back a function that restores it.

    Mustard Terminal turns d-pad presses into arrow-key codes on our input, which would
    otherwise show up as [[A and [[B. Typed text is echoed by typed() instead.
    """
    if not sys.stdin.isatty():
        return lambda: None
    saved = termios.tcgetattr(0)
    attrs = termios.tcgetattr(0)
    attrs[3] &= ~(termios.ECHO | termios.ICANON)
    termios.tcsetattr(0, termios.TCSANOW, attrs)
    return lambda: termios.tcsetattr(0, termios.TCSANOW, saved)


def flush_input():
    if sys.stdin.isatty():
        termios.tcflush(0, termios.TCIFLUSH)


# Brattoi as pixel art, two pixel rows per text row using half blocks.
POTATO = [
    "......oooooooo......",
    "....oobbbbbbbboo....",
    "...obbhhbbbbbbbbo...",
    "..obbhhbbbbbsbbbbo..",
    "..obhbbbbbbbbbbbbo..",
    ".obbbbkkbbbbkkbbbbo.",
    ".obbbbkkbbbbkkbbbbo.",
    ".obsbbbbbbbbbbbbbbo.",
    ".obbbbbbbbbbbbbsbbo.",
    ".obbbbmbbbbbbmbbbbo.",
    "..obbbbmmmmmmbbbbo..",
    "..obbbbbbbbbbbbbbo..",
    "...obbbsbbbbbbbbo...",
    "....oobbbbbbbboo....",
    "......oooooooo......",
    "....................",
]

POTATO_COLOURS = {
    "o": (90, 55, 25),
    "b": (205, 160, 95),
    "h": (235, 200, 140),
    "s": (150, 105, 55),
    "k": (20, 15, 10),
    "m": (110, 40, 30),
}


def potato_rows():
    rows = []
    for top, bottom in zip(POTATO[0::2], POTATO[1::2]):
        line = ""
        for t, b in zip(top, bottom):
            tc, bc = POTATO_COLOURS.get(t), POTATO_COLOURS.get(b)
            if tc and bc:
                line += "\x1b[38;2;%d;%d;%dm\x1b[48;2;%d;%d;%dm▀" % (tc + bc)
            elif tc:
                line += "\x1b[38;2;%d;%d;%dm\x1b[49m▀" % tc
            elif bc:
                line += "\x1b[38;2;%d;%d;%dm\x1b[49m▄" % bc
            else:
                line += "\x1b[49m "
        rows.append(line + RESET)
    return rows


SIXELS = {}


def potato_sixel(name):
    """One frame of the sixel logo (Brotato with Brattoi on his hat), within the banner's eight rows."""
    if name not in SIXELS:
        SIXELS[name] = art.sixel(art.FRAMES[name], art.scale_for(len(POTATO) // 2))
    return SIXELS[name]


def banner():
    """Potato on the left, name, tagline and model on the right. Returns the rows used."""
    side = ["", "", f"{BOLD}{MUSTARD}B R A T T O I{RESET}", f"{DIM}the tiny AI potato{RESET}", "",
            f"{CYAN}{model_label()}{RESET}", "", ""]
    if not art.enabled():
        for row, text in zip(potato_rows(), side):
            say(f"{row}  {text}")
        return len(side)
    # muxterm leaves the cursor on the row under the image; write the text beside it, then go back.
    cell_w, cell_h = art.cell_size()
    w, h = art.size_px(art.scale_for(len(POTATO) // 2))
    col = -(-w // cell_w) + 3
    sys.stdout.write(potato_sixel("open") + "\x1b7")
    for row, text in enumerate(side, 1):
        sys.stdout.write(f"\x1b[{row};{col}H{text}")
    sys.stdout.write("\x1b8")
    sys.stdout.flush()
    return -(-h // cell_h)


def header(title, note):
    """Clear the screen and draw the title or banner. Returns (rows used, banner shown)."""
    clear()
    shown = bool(note) and shutil.get_terminal_size((39, 20)).lines >= 24
    if shown:
        rows = banner()
    else:
        say(f"{BOLD}{MUSTARD}{title}{RESET}")
        rows = 1
    say(RULE)
    rows += 1
    if note:
        for line in textwrap.wrap(note, WIDTH):
            say(f"{DIM}{line}{RESET}")
            rows += 1
        say(RULE)
        rows += 1
    return rows, shown


# muxterm keeps at most 16 sixel images, and dropping the oldest leaves a stale pointer that the
# next image reallocs (sixel.c), which crashes it. The banner is one image, so redraw the whole
# screen (clearing frees them all) before the frames reach that.
MAX_FRAMES = 14
IDLE_STEP = 0


def idle_press(animate):
    """Wait for a button. With animate, the banner logo blinks and looks around meanwhile.

    Returns None when the screen needs redrawing to free muxterm's images.
    """
    global IDLE_STEP
    if not animate:
        return PAD.press()
    shown, drawn = "open", 0
    while True:
        name, secs = art.IDLE[IDLE_STEP % len(art.IDLE)]
        if name != shown:
            if drawn == MAX_FRAMES:
                return None
            # Draw over the top-left logo, then put the cursor back.
            sys.stdout.write("\x1b7\x1b[H" + potato_sixel(name) + "\x1b8")
            sys.stdout.flush()
            shown = name
            drawn += 1
        IDLE_STEP += 1
        deadline = time.monotonic() + secs
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            if select.select([PAD.fd], [], [], left)[0]:
                found = PAD.keys()
                if found:
                    return found[0]


def logo_screen(lines, text):
    """Clear, print lines (one screen row each), then the logo with text beside it.

    Returns the logo's top row (1-based) so frames can be drawn over it.
    """
    clear()
    for line in lines:
        say(line)
    # A line longer than the screen wraps onto more rows.
    columns = shutil.get_terminal_size((39, 20)).columns
    top = 1 + sum(max(1, -(-len(re.sub(r"\x1b\[[0-9;]*m", "", line)) // columns)) for line in lines)
    cell_w, _ = art.cell_size()
    col = -(-art.size_px(art.scale_for(len(POTATO) // 2))[0] // cell_w) + 3
    sys.stdout.write(potato_sixel("open") + f"\x1b7\x1b[{top + 3};{col}H{text}\x1b8")
    sys.stdout.flush()
    return top


class Busy:
    """Animate the logo on a thread while the main thread waits for llama.cpp.

    The main thread must not write until stop(). Same 14-frame cap as idle_press: the screen
    is redrawn (freeing muxterm's images) before the frames reach muxterm's limit.
    """

    def __init__(self, lines, text):
        self.lines, self.text = lines, text
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        row = logo_screen(self.lines, self.text)
        shown, drawn = "open", 0
        for name, secs in itertools.cycle(art.THINK):
            if name != shown:
                if drawn == MAX_FRAMES:
                    row = logo_screen(self.lines, self.text)
                    shown, drawn = "open", 0
                if name != shown:
                    sys.stdout.write(f"\x1b7\x1b[{row};1H" + potato_sixel(name) + "\x1b8")
                    sys.stdout.flush()
                    shown = name
                    drawn += 1
            if self.done.wait(secs):
                return

    def stop(self):
        self.done.set()
        self.thread.join()


class FirstWrite:
    """Stream wrapper that calls first() once, just before the first text arrives."""

    def __init__(self, out, first):
        self.out, self.first = out, first

    def write(self, text):
        if self.first:
            self.first()
            self.first = None
        self.out.write(text)

    def flush(self):
        self.out.flush()


def pick(title, options, back="Back", note=None):
    """Show a list and return the chosen index, or None for back."""
    if PAD.fd is None:
        return pick_by_number(title, options, back, note)

    entries = options + [back]
    current = 0
    PAD.drain()
    while True:
        rows, shown = header(title, note)
        for i, opt in enumerate(entries):
            say(f"\x1b[1;30;43m > {opt} {RESET}" if i == current else f"   {opt}")
        say(RULE)
        say(f"{DIM}Up/Down = Move   A = Select   B = Back{RESET}")

        # Only animate when nothing scrolled, so the banner is still at the top of the screen.
        fits = rows + len(entries) + 2 < shutil.get_terminal_size((39, 20)).lines
        key = idle_press(shown and art.enabled() and fits)
        if key is None:
            continue
        flush_input()
        if key == "up":
            current = (current - 1) % len(entries)
        elif key == "down":
            current = (current + 1) % len(entries)
        elif key == "b":
            return None
        elif key == "a":
            return None if current == len(options) else current


def pick_by_number(title, options, back, note=None):
    while True:
        header(title, note)
        for i, opt in enumerate(options, 1):
            say(f" {i}  {opt}")
        say(f" 0  {back}")
        say(RULE)
        try:
            choice = input("Number + Enter: ").strip()
        except EOFError:
            return None
        if choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return int(choice) - 1


def wait():
    say()
    if PAD.fd is None:
        try:
            input("Enter to go back ")
        except EOFError:
            pass
        return
    say(f"{DIM}A or B = Back   L2/R2 = Scroll{RESET}")
    PAD.drain()
    while PAD.press() not in ("a", "b"):
        pass
    flush_input()


def controls():
    """Show every button once, then mark it seen so first launch doesn't repeat it."""
    clear()
    say(f"{BOLD}{MUSTARD}Controls{RESET}")
    say(RULE)
    for button, action in CONTROLS:
        if action is None:
            say(f"{CYAN}{button}{RESET}")
        else:
            say(f" {button:<11}{action}")
    say(RULE)
    try:
        open(CONTROLS_SEEN, "w").close()
    except OSError:
        pass
    wait()


def help_answer(docs, question, logo=False):
    """Answer from the docs. With logo (the ready-made questions), the logo looks around until the
    first word; measured on Granite: 29.0 s to the first word with it, 27.8 s without."""
    clear()
    say(f"{BOLD}Q: {question}{RESET}")
    say(RULE)
    hits = ask.search(docs, question)
    if not hits:
        say("That is not in the muOS docs Brattoi knows. Try other words, or visit muos.dev.")
        return wait()
    chunks = [d for _, d in hits]
    out = AnsiOut()
    use_model_for("help")
    anim = logo and art.enabled()
    head = [f"{BOLD}{line}{RESET}" for line in textwrap.wrap(f"Q: {question}", WIDTH)] + [RULE]
    if anim:
        busy = Busy(head, f"{DIM}Thinking...{RESET}")
    else:
        say("Thinking...\n")
    first = []

    def on_first():
        first.append(True)
        if anim:
            # Clearing frees the logo's sixel images before the answer streams in.
            busy.stop()
            clear()
            for line in head:
                say(line)

    ask.answer(question, chunks, out=FirstWrite(out, on_first))
    if anim and not first:
        busy.stop()  # the model gave no answer at all
    out.finish()
    say()
    for url in dict.fromkeys(c["url"] for c in chunks):
        title = next(c["title"] for c in chunks if c["url"] == url)
        say(f"\n{CYAN}Source: {title}")
        say(f"{url}{RESET}")
    say(f"\n{DIM}muOS docs as of {DOCS_DATE}.")
    say(f"Newer info: muos.dev{RESET}")
    wait()


def conversation(title, system, first=None, logo=False):
    """Chat back and forth with one model process until an empty message is sent.

    With logo (the ready-made prompts), the logo looks around while the model wakes up.
    """
    head = [f"{BOLD}{MUSTARD}{title}{RESET}", RULE]
    use_model_for("chat")
    anim = logo and art.enabled()
    if anim:
        # Loading is mostly reading the model file, so the logo can look around meanwhile.
        busy = Busy(head, f"{DIM}Waking Brattoi up...{RESET}")
        chat = ask.Chat(system)
        busy.stop()
        clear()
        for line in head:
            say(line)
    else:
        clear()
        for line in head:
            say(line)
        sys.stdout.write(f"{DIM}Waking Brattoi up...{RESET}")
        sys.stdout.flush()
        chat = ask.Chat(system)
        # Wipe the waking line once the model is loaded.
        sys.stdout.write("\r\x1b[2K")
        sys.stdout.flush()
    try:
        if not chat.alive:
            say("Brattoi could not start. Try another model.")
            return wait()
        message = first
        turns = 0
        while True:
            if message is None:
                # Every turn, so the hint never scrolls off with the replies.
                if PAD.fd is not None:
                    say(f"{DIM}{TYPE_HINT}{RESET}")
                message = read_line(f"{BOLD}{MUSTARD}You:{RESET} ")
                if not message:
                    return
            else:
                say(f"{BOLD}{MUSTARD}You:{RESET} {message}")
            sys.stdout.write(f"{BOLD}{CYAN}Brattoi:{RESET} ")
            out = AnsiOut()
            reply = chat.say(message, out=out)
            out.finish()
            say("\n")
            if reply is None:
                # llama.cpp stops when the context is full (context shift is off by default).
                say("Brattoi's memory is full. Start a new chat." if turns else
                    "Brattoi stopped talking. Back to the menu.")
                return wait()
            turns += 1
            message = None
    finally:
        chat.close()


def typed(label):
    clear()
    say(label)
    say(RULE)
    if PAD.fd is not None:
        say(f"{DIM}Select = Keyboard (again: move, hide){RESET}")
        say(f"{DIM}X/Start = Send   Send empty = Back{RESET}")
    return read_line("> ")


def read_line(prompt):
    """Read one line from the on-screen keyboard or a USB keyboard; X or Start submits."""
    if PAD.fd is None:
        try:
            return input(prompt).strip()
        except EOFError:
            return ""

    # Mustard Terminal's keyboard (bottom) only shows the top half of the screen, so scroll the
    # text up and put the prompt above it. Lines that scroll off still go to the scrollback.
    lift = shutil.get_terminal_size((39, 20)).lines // 2 + 3
    sys.stdout.write("\n" * lift + f"\x1b[{lift}A" + prompt)
    sys.stdout.flush()
    flush_input()
    PAD.drain()

    text = ""
    while True:
        ready, _, _ = select.select([0, PAD.fd], [], [])
        if PAD.fd in ready and "x" in PAD.keys():
            break
        if 0 not in ready:
            continue
        data = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", os.read(0, 64).decode("utf-8", "ignore"))
        if "\r" in data or "\n" in data:
            break
        for ch in data:
            if ch in "\x7f\b":
                if text:
                    text = text[:-1]
                    sys.stdout.write("\b \b")
            elif ch.isprintable():
                text += ch
                sys.stdout.write(ch)
        sys.stdout.flush()
    say()
    return text.strip()


def model_name(path):
    if not path:
        return "none"
    base = os.path.basename(path)
    for entry in models.catalogue():
        if entry["file"] == base:
            return entry["name"]
    return base.replace(".gguf", "")


# Auto: Granite answers help questions (sticks to the docs), Qwen holds the chats (best talker).
AUTO_FILES = {"help": "granite-4.0-350m-Q4_0.gguf", "chat": "Qwen2.5-0.5B-Instruct-Q4_0.gguf"}
AUTO_LABEL = "Auto: Granite help, Qwen chat"
AUTO = False


def auto_ready():
    return all(os.path.isfile(os.path.join(ask.HERE, f)) for f in AUTO_FILES.values())


def model_label():
    return AUTO_LABEL if AUTO else model_name(ask.MODEL)


def use_model_for(task):
    """In Auto, point ask.MODEL at the help or chat model before it runs."""
    if AUTO:
        ask.MODEL = os.path.join(ask.HERE, AUTO_FILES[task])


def set_model(entry):
    global AUTO
    AUTO = False
    ask.MODEL = models.path(entry) if entry else None
    if entry:
        with open(MODEL_SETTING, "w") as fh:
            fh.write(entry["file"])
    elif os.path.exists(MODEL_SETTING):
        os.remove(MODEL_SETTING)


def set_auto():
    global AUTO
    AUTO = True
    use_model_for("help")
    with open(MODEL_SETTING, "w") as fh:
        fh.write("auto")


def use_saved_model():
    """Pick the saved model (or Auto). Nothing saved: Auto when it can run. Else any installed one."""
    try:
        with open(MODEL_SETTING) as fh:
            saved = fh.read().strip()
    except OSError:
        saved = None
    if saved and saved != "auto" and os.path.isfile(os.path.join(ask.HERE, saved)):
        set_model({"file": saved})
        return
    if (saved is None or saved == "auto") and auto_ready():
        set_auto()
        return
    present = [e for e in models.catalogue() if models.installed(e)]
    set_model(present[0] if present else None)


def show_progress(done, total, rate):
    if done is None:
        sys.stdout.write("\r\x1b[2KChecking the download...")
    else:
        bar_width = max(10, WIDTH - 34)
        filled = bar_width * done // total
        bar = "#" * filled + "-" * (bar_width - filled)
        sys.stdout.write(
            f"\r\x1b[2K[{MUSTARD}{bar}{RESET}] {100 * done // total:3d}%  "
            f"{done / 1e6:.0f}/{total / 1e6:.0f} MB  {rate / 1e6:.1f} MB/s"
        )
    sys.stdout.flush()


DOWNLOAD_RESULTS = {
    "ok": "Downloaded and checked. Ready to use.",
    "cancelled": "Cancelled. Download again to carry on from where it stopped.",
    "offline": "No internet. Connect WiFi in the muOS settings, then try again.",
    "space": "Not enough free space on the SD card.",
    "bad": "The download was damaged and has been removed. Try again.",
}


def download_model(entry):
    clear()
    say(f"{BOLD}Downloading {entry['name']}{RESET}")
    say(RULE)
    say(f"{DIM}{models.megabytes(entry['size'])} from Hugging Face   B = Cancel{RESET}\n")
    PAD.drain()
    cancelled = (lambda: "b" in PAD.keys()) if PAD.fd is not None else (lambda: False)
    result = models.download(entry, show_progress, cancelled)
    say("\n")
    say(DOWNLOAD_RESULTS.get(result, f"Download failed ({result}). Try again."))
    if result == "ok" and not ask.MODEL:
        set_model(entry)
    wait()


def in_use(entry):
    """True when Brattoi runs this model: the single active one, or one of the Auto pair."""
    if AUTO:
        return entry["file"] in AUTO_FILES.values()
    return models.path(entry) == ask.MODEL


def manage_models():
    while True:
        entries = models.catalogue()
        labels = [f"{'Auto':<17} {'Granite + Qwen':<14} "
                  f"[{'Active' if AUTO else 'Ready' if auto_ready() else 'Needs both'}]"]
        for e in entries:
            if models.installed(e):
                status = ("Auto" if AUTO else "Active") if in_use(e) else "Installed"
            else:
                status = f"Download {models.megabytes(e['size'])}"
            labels.append(f"{e['name']:<17} {e.get('rating', 'Untested'):<14} [{status}]")

        choice = pick("Models", labels, note="Best for help first. Better answers are slower. "
                      "Models download over WiFi from Hugging Face.")
        if choice is None:
            return
        if choice == 0:
            info = ("Granite answers muOS questions, Qwen does the chats. Each wakes up when it is "
                    "needed.")
            if AUTO:
                pick(AUTO_LABEL, [], note=info + " In use now.")
            elif not auto_ready():
                pick(AUTO_LABEL, [], note=info + " Download Granite 4.0 350M and Qwen2.5 0.5B first.")
            elif pick(AUTO_LABEL, ["Use"], note=info) == 0:
                set_auto()
            continue
        entry = entries[choice - 1]
        info = f"{entry['note']} License: {entry['license']}."

        if not models.installed(entry):
            if pick(entry["name"], [f"Download ({models.megabytes(entry['size'])})"], note=info) == 0:
                download_model(entry)
            continue

        actions = ["Delete"] if in_use(entry) and not AUTO else ["Use", "Delete"]
        action = pick(entry["name"], actions, note=info)
        if action is None:
            continue
        if actions[action] == "Use":
            set_model(entry)
        elif pick(f"Delete {entry['name']}?", ["Yes, delete it"], back="No") == 0:
            was_in_use = in_use(entry)
            models.delete(entry)
            if was_in_use:
                use_saved_model()


def needs_model():
    """Return True when a model is ready, otherwise offer the model manager."""
    if ask.MODEL and os.path.isfile(ask.MODEL):
        return True
    if pick("Brattoi needs a brain", ["Get a model"],
            note="No model is installed yet. Download one over WiFi to start chatting.") == 0:
        manage_models()
    return bool(ask.MODEL and os.path.isfile(ask.MODEL))


def main():
    docs = ask.load()
    use_saved_model()
    restore = quiet_input() if PAD.fd is not None else (lambda: None)
    try:
        if PAD.fd is not None and not os.path.exists(CONTROLS_SEEN):
            controls()
        if not ask.MODEL:
            needs_model()
        return menu(docs)
    finally:
        restore()


def menu(docs):
    while True:
        choice = pick("Brattoi", [
            "Ask muOS",
            "Just for fun",
            "Ask muOS your own question",
            "Free chat",
            f"Model: {model_label()}",
            "Controls",
        ], back="Quit", note=WARNING)

        if choice is None:
            return 0
        if choice == 4:
            manage_models()
            continue
        if choice == 5:
            controls()
            continue
        if not needs_model():
            continue
        if choice == 0:
            q = pick("Ask muOS", HELP_QUESTIONS)
            if q is not None:
                help_answer(docs, HELP_QUESTIONS[q], logo=True)
        elif choice == 1:
            p = pick("Just for fun", FUN_PROMPTS)
            if p is not None:
                conversation("Just for fun", FUN_SYSTEM, first=FUN_PROMPTS[p], logo=True)
        elif choice == 2:
            q = typed("Your muOS question")
            if q:
                help_answer(docs, q)
        elif choice == 3:
            conversation("Free chat", CHAT_SYSTEM)


if __name__ == "__main__":
    sys.exit(main())
