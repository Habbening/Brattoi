#!/usr/bin/env python3
"""Brattoi's screens: menus, chats, help answers and the model manager, drawn with the muOS SDL
and driven by the handheld's buttons.

A: select. B: back. In a chat, A or X: write. Up/Down: scroll. Menu: quit.
Keyboard: D-pad + A types, B deletes, Y is space, X is shift, L1/R1 change symbols, Start sends.

The menu texts, prompts and model choice are the terminal version's (chat.py), which still works
on its own.
"""
import os
import queue
import re
import sys
import threading
import time

# chat.py reads the buttons itself when it runs in the terminal. Here SDL does.
os.environ["BRATTOI_NO_PAD"] = "1"

import art  # noqa: E402
import ask  # noqa: E402
import chat  # noqa: E402
import models  # noqa: E402
import sdl  # noqa: E402

FONT_PATHS = (
    "/opt/muos/share/font/NotoSans/NotoSans-SemiBold.ttf",
    "/opt/muos/share/font/internal/Inter Display.ttf",
)
REPEAT_DELAY, REPEAT_EVERY = 0.35, 0.07
REDRAW_EVERY = 0.5
MESSAGE_LIMIT = 300

BG, PANEL, LINE = (18, 17, 15), (38, 35, 30), (62, 57, 48)
TEXT, DIM, ACCENT = (240, 236, 228), (160, 152, 138), (240, 190, 40)
MINE, THEIRS, LINK, BAD = (92, 70, 16), (46, 43, 37), (110, 190, 220), (230, 90, 90)

# muOS-Keys raw joystick numbers, used only when SDL has no controller mapping for the pad.
RAW_BUTTONS = {3: "ok", 2: "back", 5: "x", 4: "y", 6: "l1", 7: "r1", 11: "start", 12: "menu"}

CONTROLS = [
    ("Menus", None),
    ("Up / Down", "Move"),
    ("A", "Select"),
    ("B", "Back"),
    ("Chat", None),
    ("A or X", "Write a message"),
    ("Up / Down", "Scroll"),
    ("B", "End chat, go back"),
    ("Keyboard", None),
    ("D-pad + A", "Type a key"),
    ("B", "Delete"),
    ("Y", "Space"),
    ("X", "Shift"),
    ("L1 / R1", "Symbols"),
    ("Start", "Send"),
    ("Other", None),
    ("Menu", "Quit Brattoi"),
]


def find_font():
    for path in (os.environ.get("BRATTOI_FONT", ""),) + FONT_PATHS:
        if path and os.path.exists(path):
            return path
    raise SystemExit("Brattoi: no font found")


class Fonts:
    def __init__(self, height):
        path = find_font()
        scale = height / 480
        self.big = sdl.Font(path, round(26 * scale))
        self.normal = sdl.Font(path, round(21 * scale))
        self.small = sdl.Font(path, round(16 * scale))
        self.scale = scale


def fit(font, text, width):
    """text, cut with an ellipsis so it fits in width."""
    if font.size(text)[0] <= width:
        return text
    while text and font.size(text + "…")[0] > width:
        text = text[:-1]
    return text + "…"


def wrap(font, text, width):
    """Lines that fit in width. Long words are broken."""
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if font.size(candidate)[0] <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            while font.size(word)[0] > width and len(word) > 1:
                cut = len(word)
                while cut > 1 and font.size(word[:cut])[0] > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


def plain(text):
    """The model's markdown as plain text: bullets become dots, the other marks go."""
    text = re.sub(r"[\x00-\x09\x0b-\x1f]", "", text)
    text = re.sub(r"(?m)^\s*#+\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*]\s+", "• ", text)
    return re.sub(r"\*+|`", "", text).strip()


class Logo:
    """The pixel-art logo (art.py), one picture per face."""

    def __init__(self, scale):
        self.scale = max(1, round(2 * scale))
        self.width, self.height = art.W * self.scale, art.H * self.scale
        self.frames = {}

    def frame(self, name):
        if name not in self.frames:
            picture = sdl.blank(self.width, self.height)
            for y, row in enumerate(art.FRAMES[name]):
                for x, colour in enumerate(row):
                    if colour is not None:
                        sdl.draw_rect(picture, art.PALETTE[colour],
                                      (x * self.scale, y * self.scale, self.scale, self.scale))
            self.frames[name] = picture
        return self.frames[name]


class Faces:
    """Steps through one of art.py's face sequences (IDLE or THINK) in real time."""

    def __init__(self, steps):
        self.steps, self.index = steps, 0
        self.until = time.monotonic() + steps[0][1]

    @property
    def name(self):
        return self.steps[self.index][0]

    def tick(self):
        if time.monotonic() < self.until:
            return False
        self.index = (self.index + 1) % len(self.steps)
        self.until = time.monotonic() + self.steps[self.index][1]
        return True


class Screen:
    title = "Brattoi"
    hints = ""

    def __init__(self, app):
        self.app = app

    def handle(self, action):
        pass

    def draw(self, surface, area):
        pass

    def tick(self):
        """Called on every loop. Return True to redraw."""
        return False

    def close(self):
        """Called when the screen leaves the stack."""


class ListScreen(Screen):
    def __init__(self, app):
        super().__init__(app)
        self.selected = 0
        self.top = 0

    def row_height(self):
        return round(self.app.fonts.normal.get_height() * 1.4)

    def move(self, action, count):
        if not count:
            return
        if action == "up":
            self.selected = (self.selected - 1) % count
        elif action == "down":
            self.selected = (self.selected + 1) % count

    def draw_rows(self, surface, area, rows, draw_row):
        height = self.row_height()
        visible = max(1, area.height // height)
        self.selected = min(self.selected, max(0, len(rows) - 1))
        if self.selected < self.top:
            self.top = self.selected
        elif self.selected >= self.top + visible:
            self.top = self.selected - visible + 1
        for slot, index in enumerate(range(self.top, min(len(rows), self.top + visible))):
            rect = sdl.Rect(area.x, area.y + slot * height, area.width, height)
            if index == self.selected:
                sdl.draw_rect(surface, PANEL, rect, border_radius=round(6 * self.app.fonts.scale))
                sdl.draw_rect(surface, ACCENT, (rect.x, rect.y + 4, 4, rect.height - 8))
            draw_row(surface, rect, rows[index], index == self.selected)


class Menu(ListScreen):
    """A list to pick from, with an optional note under it and the logo above it.

    options is a list, or a function that returns one (for labels that change).
    """

    def __init__(self, app, title, options, on_pick, note="", logo=False):
        super().__init__(app)
        self.title, self.options, self.on_pick, self.note = title, options, on_pick, note
        self.faces = Faces(art.IDLE) if logo else None
        self.hints = "A: select    B: quit" if logo else "A: select    B: back"
        self.row = super().row_height()

    def row_height(self):
        return self.row

    def labels(self):
        return self.options() if callable(self.options) else self.options

    def tick(self):
        return bool(self.faces) and self.faces.tick()

    def handle(self, action):
        labels = self.labels()
        self.move(action, len(labels))
        if action in ("ok", "start") and labels:
            self.on_pick(min(self.selected, len(labels) - 1))
        elif action == "back":
            self.app.pop()

    def draw(self, surface, area):
        fonts = self.app.fonts
        y = area.y
        if self.faces:
            logo = self.app.logo
            surface.blit(logo.frame(self.faces.name), (area.x, y))
            x = area.x + logo.width + round(20 * fonts.scale)
            surface.blit(fonts.big.render("B R A T T O I", ACCENT), (x, y + round(8 * fonts.scale)))
            line = y + round(8 * fonts.scale) + fonts.big.get_linesize()
            surface.blit(fonts.small.render("the tiny AI potato", DIM), (x, line))
            surface.blit(fonts.small.render(fit(fonts.small, chat.model_label(), area.right - x), LINK),
                         (x, line + fonts.small.get_linesize()))
            y += logo.height + round(8 * fonts.scale)
        note = wrap(fonts.small, self.note, area.width) if self.note else []
        note_height = len(note) * fonts.small.get_linesize()
        rows = sdl.Rect(area.x, y, area.width, area.bottom - y - note_height)
        labels = self.labels()
        # Rows close up so the whole list shows, down to the height of the text.
        self.row = max(fonts.normal.get_height(), min(super().row_height(), rows.height // max(1, len(labels))))

        def row(surf, rect, label, selected):
            surf.blit(fonts.normal.render(fit(fonts.normal, label, rect.width - 28), TEXT if selected else DIM),
                      (rect.x + 14, rect.centery - fonts.normal.get_height() // 2))
        self.draw_rows(surface, rows, labels, row)
        for i, text in enumerate(note):
            surface.blit(fonts.small.render(text, DIM), (area.x, area.bottom - note_height + i * fonts.small.get_linesize()))


class ControlsScreen(Screen):
    title = "Controls"
    hints = "A or B: back"

    def __init__(self, app):
        super().__init__(app)
        # Shown once on first launch; after that from the menu.
        try:
            open(chat.CONTROLS_SEEN, "w").close()
        except OSError:
            pass

    def handle(self, action):
        if action in ("ok", "back", "start"):
            self.app.pop()

    def draw(self, surface, area):
        font = self.app.fonts.small
        step = min(font.get_linesize(), area.height // len(CONTROLS))
        for i, (button, does) in enumerate(CONTROLS):
            y = area.y + i * step
            if does is None:
                surface.blit(font.render(button, ACCENT), (area.x, y))
            else:
                surface.blit(font.render(button, TEXT), (area.x + round(16 * self.app.fonts.scale), y))
                surface.blit(font.render(does, DIM), (area.x + area.width * 2 // 5, y))


class Talk(Screen):
    """A help answer (question given) or a chat (system prompt given), as speech bubbles.

    llama.cpp runs on a thread and posts what it says to a queue; the screen reads it in tick().
    """

    def __init__(self, app, title, system=None, first=None, question=None):
        super().__init__(app)
        self.title = title
        self.entries = []  # [who, text]: who is "You", "Brattoi" or "" (a note)
        self.events = queue.Queue()
        self.is_chat = question is None
        self.chat = None
        self.proc = None
        self.closed = False
        self.waiting = False  # the model is working
        self.busy = ""  # shown with the logo until the model's first words
        self.streaming = False  # the last entry is the reply being written
        self.done = not self.is_chat
        self.turns = 0
        self.scroll = 0  # lines up from the newest
        self.wrapped = {}
        self.faces = Faces(art.THINK)
        if self.is_chat:
            chat.use_model_for("chat")
            self.waiting, self.busy = True, "Waking Brattoi up..."
            self.start(self.run_wake, system, first)
            return
        self.entries.append(["You", question])
        hits = ask.search(app.docs, question)
        if not hits:
            self.entries.append(["Brattoi", "That is not in the muOS docs Brattoi knows. Try other words, "
                                            "or visit muos.dev."])
            return
        chat.use_model_for("help")
        self.waiting, self.busy = True, "Thinking..."
        self.start(self.run_help, question, [doc for _, doc in hits])

    @property
    def hints(self):
        if self.is_chat and not self.done and not self.waiting:
            return "A or X: write    Up/Down: scroll    B: end chat"
        return "Up/Down: scroll    B: back"

    def start(self, target, *args):
        threading.Thread(target=target, args=args, daemon=True).start()

    # The model's side, on a thread. write() and flush() make this screen the stream it writes to.
    def write(self, text):
        self.events.put(("text", text))

    def flush(self):
        pass

    def run_help(self, question, chunks):
        ask.answer(question, chunks, out=self, on_proc=lambda proc: setattr(self, "proc", proc))
        self.events.put(("answered", chunks))

    def run_wake(self, system, first):
        talk = ask.Chat(system)
        if self.closed:
            talk.close()
        else:
            self.events.put(("awake", talk, first))

    def run_say(self, message):
        self.events.put(("replied", self.chat.say(message, out=self)))

    # The screen's side.
    def send(self, message):
        self.entries.append(["You", message])
        self.waiting, self.busy, self.streaming, self.scroll = True, "Thinking...", False, 0
        self.start(self.run_say, message)

    def note(self, text):
        self.entries.append(["", text])
        self.done = True

    def tick(self):
        changed = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            changed = True
            kind = event[0]
            if kind == "text":
                if not self.streaming:
                    self.entries.append(["Brattoi", ""])
                    self.streaming, self.busy = True, ""
                self.entries[-1][1] += event[1]
            elif kind == "answered":
                self.waiting, self.busy = False, ""
                if not self.streaming:
                    self.note("Brattoi gave no answer. Try again, or try another model.")
                sources = dict.fromkeys((c["title"], c["url"]) for c in event[1])
                lines = [f"Source: {title}\n{url}" for title, url in sources]
                self.note("\n".join(lines + [f"muOS docs as of {chat.DOCS_DATE}. Newer info: muos.dev"]))
            elif kind == "awake":
                self.chat = event[1]
                self.waiting, self.busy = False, ""
                if not self.chat.alive:
                    self.note("Brattoi could not start. Try another model.")
                elif event[2]:
                    self.send(event[2])
            elif kind == "replied":
                self.waiting, self.busy, self.streaming = False, "", False
                if event[1] is None:
                    # llama.cpp stops when the context is full (context shift is off by default).
                    self.note("Brattoi's memory is full. Start a new chat." if self.turns else
                              "Brattoi stopped talking.")
                else:
                    self.turns += 1
        if self.busy and self.faces.tick():
            changed = True
        return changed

    def handle(self, action):
        if action in ("ok", "x", "start"):
            if self.is_chat and not self.done and not self.waiting:
                self.app.push(KeyboardScreen(self.app, self.send))
        elif action == "up":
            self.scroll += 1
        elif action == "down":
            self.scroll = max(0, self.scroll - 1)
        elif action == "back":
            self.app.pop()

    def close(self):
        self.closed = True
        for proc in (self.chat.proc if self.chat else None, self.proc):
            if proc and proc.poll() is None:
                proc.terminate()

    def lines(self, index, width):
        """The wrapped lines of one entry, kept until its text changes."""
        who, text = self.entries[index]
        key = (len(text), width)
        if self.wrapped.get(index, (None,))[0] != key:
            shown = plain(text) if who == "Brattoi" else text
            self.wrapped[index] = (key, wrap(self.app.fonts.small if not who else self.app.fonts.normal, shown, width))
        return self.wrapped[index][1]

    def draw(self, surface, area):
        fonts = self.app.fonts
        pad = round(10 * fonts.scale)
        bottom = area.bottom
        if self.busy:
            logo = self.app.logo
            bottom -= logo.height
            surface.blit(logo.frame(self.faces.name), (area.x, bottom))
            surface.blit(fonts.normal.render(self.busy, DIM),
                         (area.x + logo.width + pad, bottom + (logo.height - fonts.normal.get_height()) // 2))
            bottom -= pad
        inner = int((area.width - 2 * pad) * 0.86)
        line, small = fonts.normal.get_linesize(), fonts.small.get_linesize()
        blocks = []
        for index, (who, _) in enumerate(self.entries):
            lines = self.lines(index, inner if who else area.width)
            height = (small + len(lines) * line + 2 * pad if who else len(lines) * small) + pad
            blocks.append((who, lines, height))
        total = sum(height for _, _, height in blocks)
        view = bottom - area.y
        self.scroll = min(self.scroll, max(0, (total - view) // line + 1))
        y = bottom - total + self.scroll * line if total > view else area.y
        clip = surface.get_clip()
        surface.set_clip(sdl.Rect(area.x, area.y, area.width, view))
        for who, lines, height in blocks:
            if y + height >= area.y and y <= bottom:
                if not who:
                    for i, text in enumerate(lines):
                        colour = LINK if text.startswith("http") else DIM
                        surface.blit(fonts.small.render(text, colour), (area.x, y + i * small))
                else:
                    mine = who == "You"
                    width = max([fonts.normal.size(t)[0] for t in lines] + [fonts.small.size(who)[0]]) + 2 * pad
                    x = area.right - width if mine else area.x
                    sdl.draw_rect(surface, MINE if mine else THEIRS, (x, y, width, height - pad),
                                  border_radius=round(10 * fonts.scale))
                    surface.blit(fonts.small.render(who, ACCENT if mine else LINK), (x + pad, y + pad // 2))
                    for i, text in enumerate(lines):
                        surface.blit(fonts.normal.render(text, TEXT), (x + pad, y + pad // 2 + small + i * line))
            y += height
        surface.set_clip(clip)


KEY_ROWS = {
    "abc": ["1234567890", "qwertyuiop", "asdfghjkl'", "zxcvbnm,.?"],
    "ABC": ["1234567890", "QWERTYUIOP", "ASDFGHJKL'", "ZXCVBNM,.?"],
    "#+=": ["!@#$%^&*()", "-_=+[]{};:", "/\\|<>\"~`€£", "°…•·¡¿«»€!"],
}
SPECIAL = ["Shift", "#+=", "Space", "Del", "Send"]


class KeyboardScreen(Screen):
    title = "Write"
    hints = "A: type   B: delete   Y: space   X: shift   Start: send"

    def __init__(self, app, on_send):
        super().__init__(app)
        self.on_send = on_send
        self.text = ""
        self.layer = "abc"
        self.row = 1
        self.col = 0

    def type(self, char):
        if len(self.text) < MESSAGE_LIMIT:
            self.text += char

    def press(self):
        if self.row < 4:
            self.type(KEY_ROWS[self.layer][self.row][self.col])
            if self.layer == "ABC":
                self.layer = "abc"
            return
        key = SPECIAL[min(self.col * len(SPECIAL) // 10, len(SPECIAL) - 1)]
        if key == "Shift":
            self.layer = "ABC" if self.layer == "abc" else "abc"
        elif key == "#+=":
            self.layer = "abc" if self.layer == "#+=" else "#+="
        elif key == "Space":
            self.type(" ")
        elif key == "Del":
            self.text = self.text[:-1]
        elif key == "Send":
            self.send()

    def send(self):
        text = self.text.strip()
        self.app.pop()
        if text:
            self.on_send(text)

    def handle(self, action):
        if action == "up":
            self.row = (self.row - 1) % 5
        elif action == "down":
            self.row = (self.row + 1) % 5
        elif action == "left":
            self.col = (self.col - (1 if self.row < 4 else 2)) % 10
        elif action == "right":
            self.col = (self.col + (1 if self.row < 4 else 2)) % 10
        elif action == "ok":
            self.press()
        elif action == "back":
            if self.text:
                self.text = self.text[:-1]
            else:
                self.app.pop()
        elif action == "y":
            self.type(" ")
        elif action == "x":
            self.layer = "ABC" if self.layer == "abc" else "abc"
        elif action in ("l1", "r1"):
            self.layer = "abc" if self.layer == "#+=" else "#+="
        elif action == "start":
            self.send()
        if self.row == 4:
            self.col -= self.col % 2

    def draw(self, surface, area):
        fonts = self.app.fonts
        pad = round(8 * fonts.scale)
        field = sdl.Rect(area.x, area.y, area.width, fonts.normal.get_linesize() * 3 + 2 * pad)
        sdl.draw_rect(surface, PANEL, field, border_radius=round(8 * fonts.scale))
        lines = wrap(fonts.normal, self.text + "_", field.width - 2 * pad)[-3:]
        for i, line in enumerate(lines):
            surface.blit(fonts.normal.render(line, TEXT), (field.x + pad, field.y + pad + i * fonts.normal.get_linesize()))
        count = fonts.small.render(f"{len(self.text)}/{MESSAGE_LIMIT}", DIM)
        surface.blit(count, count.get_rect(topright=(field.right, field.bottom + 2)))
        top = field.bottom + fonts.small.get_linesize() + pad
        key_h = (area.bottom - top) // 5
        key_w = area.width // 10
        for r in range(5):
            y = top + r * key_h
            if r < 4:
                cells = [(c, 1, ch) for c, ch in enumerate(KEY_ROWS[self.layer][r])]
            else:
                cells = [(c * 2, 2, label) for c, label in enumerate(SPECIAL)]
            for col, span, label in cells:
                rect = sdl.Rect(area.x + col * key_w + 2, y + 2, key_w * span - 4, key_h - 4)
                chosen = r == self.row and (col == self.col if span == 1 else col == self.col - self.col % 2)
                lit = label == "Shift" and self.layer == "ABC" or label == "#+=" and self.layer == "#+="
                sdl.draw_rect(surface, ACCENT if chosen else LINE if lit else PANEL, rect,
                              border_radius=round(6 * fonts.scale))
                font = fonts.normal if span == 1 else fonts.small
                image = font.render(label, BG if chosen else TEXT)
                surface.blit(image, image.get_rect(center=rect.center))


class ModelsScreen(ListScreen):
    title = "Models"
    hints = "A: select    B: back"
    NOTE = "Best for help first. Better answers are slower. Models download over WiFi from Hugging Face."

    def rows(self):
        """(name, what it is good at, state, catalogue entry or None for Auto)."""
        state = "Active" if chat.AUTO else "Ready" if chat.auto_ready() else "Needs both"
        rows = [("Auto", "Granite for help, Qwen for chat", state, None)]
        for entry in models.catalogue():
            if models.installed(entry):
                state = ("Auto" if chat.AUTO else "Active") if chat.in_use(entry) else "Installed"
            else:
                state = f"Download {models.megabytes(entry['size'])}"
            rows.append((entry["name"], entry.get("rating", "Untested"), state, entry))
        return rows

    def row_height(self):
        fonts = self.app.fonts
        return fonts.normal.get_linesize() + fonts.small.get_linesize() + round(6 * fonts.scale)

    def handle(self, action):
        rows = self.rows()
        self.move(action, len(rows))
        if action in ("ok", "start"):
            self.open(rows[min(self.selected, len(rows) - 1)][3])
        elif action == "back":
            self.app.pop()

    def open(self, entry):
        app = self.app
        if entry is None:
            info = "Granite answers muOS questions, Qwen does the chats. Each wakes up when it is needed."
            if chat.AUTO:
                app.push(Menu(app, chat.AUTO_LABEL, [], None, note=info + " In use now."))
            elif not chat.auto_ready():
                app.push(Menu(app, chat.AUTO_LABEL, [], None,
                              note=info + " Download Granite 4.0 350M and Qwen2.5 0.5B first."))
            else:
                app.push(Menu(app, chat.AUTO_LABEL, ["Use"], lambda _: (chat.set_auto(), app.pop()), note=info))
            return
        info = f"{entry['note']} License: {entry['license']}."
        if not models.installed(entry):
            app.push(Menu(app, entry["name"], [f"Download ({models.megabytes(entry['size'])})"],
                          lambda _: app.replace(DownloadScreen(app, entry)), note=info))
            return
        actions = ["Delete"] if chat.in_use(entry) and not chat.AUTO else ["Use", "Delete"]

        def act(index):
            if actions[index] == "Use":
                chat.set_model(entry)
                app.pop()
            else:
                app.replace(Menu(app, f"Delete {entry['name']}?", ["No", "Yes, delete it"], delete))

        def delete(index):
            if index == 1:
                was_in_use = chat.in_use(entry)
                models.delete(entry)
                if was_in_use:
                    chat.use_saved_model()
            app.pop()
        app.push(Menu(app, entry["name"], actions, act, note=info))

    def draw(self, surface, area):
        fonts = self.app.fonts
        note = wrap(fonts.small, self.NOTE, area.width)
        note_height = len(note) * fonts.small.get_linesize()
        pad = round(14 * fonts.scale)

        def row(surf, rect, item, selected):
            name, good_at, state, _ = item
            label = fonts.small.render(state, ACCENT if state in ("Active", "Auto") else DIM)
            surf.blit(label, label.get_rect(topright=(rect.right - pad, rect.y + 6)))
            surf.blit(fonts.normal.render(fit(fonts.normal, name, rect.width - 3 * pad - label.get_width()), TEXT),
                      (rect.x + pad, rect.y + 3))
            surf.blit(fonts.small.render(fit(fonts.small, good_at, rect.width - 2 * pad), DIM),
                      (rect.x + pad, rect.y + 3 + fonts.normal.get_linesize()))
        self.draw_rows(surface, sdl.Rect(area.x, area.y, area.width, area.height - note_height), self.rows(), row)
        for i, text in enumerate(note):
            surface.blit(fonts.small.render(text, DIM), (area.x, area.bottom - note_height + i * fonts.small.get_linesize()))


class DownloadScreen(Screen):
    def __init__(self, app, entry):
        super().__init__(app)
        self.entry = entry
        self.title = f"Downloading {entry['name']}"
        self.progress = (0, entry["size"], 0)  # bytes done, total, bytes a second; done None: checking
        self.shown = None
        self.result = None
        self.cancel = False
        threading.Thread(target=self.run, daemon=True).start()

    @property
    def hints(self):
        return "A or B: back" if self.result else "B: cancel"

    def run(self):
        result = models.download(self.entry, lambda *state: setattr(self, "progress", state), lambda: self.cancel)
        if result == "ok" and not ask.MODEL:
            chat.set_model(self.entry)
        self.result = result

    def tick(self):
        now = (self.progress, self.result)
        changed, self.shown = now != self.shown, now
        return changed

    def handle(self, action):
        if self.result and action in ("ok", "back", "start"):
            self.app.pop()
        elif action == "back":
            self.cancel = True

    def close(self):
        self.cancel = True

    def draw(self, surface, area):
        fonts = self.app.fonts
        done, total, rate = self.progress
        line = fonts.normal.get_linesize()
        surface.blit(fonts.small.render(f"{models.megabytes(total)} from Hugging Face", DIM), area.topleft)
        bar = sdl.Rect(area.x, area.y + 2 * line, area.width, round(22 * fonts.scale))
        sdl.draw_rect(surface, PANEL, bar, border_radius=bar.height // 2)
        share = 1 if done is None else done / total if total else 0
        if share > 0:
            sdl.draw_rect(surface, ACCENT, (bar.x, bar.y, max(bar.height, int(bar.width * share)), bar.height),
                          border_radius=bar.height // 2)
        if self.result:
            text = chat.DOWNLOAD_RESULTS.get(self.result, f"Download failed ({self.result}). Try again.")
        elif done is None:
            text = "Checking the download..."
        else:
            text = f"{100 * done // total}%   {done / 1e6:.0f} of {total / 1e6:.0f} MB   {rate / 1e6:.1f} MB/s"
        for i, part in enumerate(wrap(fonts.normal, text, area.width)):
            surface.blit(fonts.normal.render(part, TEXT), (area.x, bar.bottom + line + i * line))


class App:
    def __init__(self, surface):
        self.surface = surface
        self.fonts = Fonts(surface.get_height())
        self.logo = Logo(self.fonts.scale)
        self.docs = ask.load()
        chat.use_saved_model()
        self.exit_code = None
        self.held = None
        self.controllers = set()  # pads SDL has a controller mapping for
        self.stack = [Menu(self, "Brattoi", self.home_items, self.home_pick, note=chat.WARNING, logo=True)]
        if not ask.MODEL:
            self.model_ready()
        if not os.path.exists(chat.CONTROLS_SEEN):
            self.push(ControlsScreen(self))

    # Screen stack
    @property
    def screen(self):
        return self.stack[-1]

    def push(self, screen):
        self.stack.append(screen)

    def pop(self):
        """Leave the top screen. Leaving the first one quits."""
        self.stack.pop().close()
        if not self.stack:
            self.exit_code = 0

    def replace(self, screen):
        self.stack.pop().close()
        self.stack.append(screen)

    # The main menu
    def home_items(self):
        return ["Ask muOS", "Just for fun", "Ask muOS your own question", "Free chat",
                f"Model: {chat.model_label()}", "Controls", "Quit"]

    def model_ready(self):
        """True when a model is installed. Otherwise offer the model manager."""
        if ask.MODEL and os.path.isfile(ask.MODEL):
            return True
        self.push(Menu(self, "Brattoi needs a brain", ["Get a model"], lambda _: self.replace(ModelsScreen(self)),
                       note="No model is installed yet. Download one over WiFi to start chatting."))
        return False

    def home_pick(self, index):
        if index == 4:
            self.push(ModelsScreen(self))
        elif index == 5:
            self.push(ControlsScreen(self))
        elif index == 6:
            self.exit_code = 0
        elif not self.model_ready():
            pass
        elif index == 0:
            self.push(Menu(self, "Ask muOS", chat.HELP_QUESTIONS,
                           lambda i: self.push(Talk(self, "Ask muOS", question=chat.HELP_QUESTIONS[i]))))
        elif index == 1:
            self.push(Menu(self, "Just for fun", chat.FUN_PROMPTS,
                           lambda i: self.push(Talk(self, "Just for fun", chat.FUN_SYSTEM, first=chat.FUN_PROMPTS[i]))))
        elif index == 2:
            self.push(KeyboardScreen(self, lambda text: self.push(Talk(self, "Ask muOS", question=text))))
        elif index == 3:
            self.push(Talk(self, "Free chat", chat.CHAT_SYSTEM))

    # Drawing
    def draw(self):
        surface, fonts = self.surface, self.fonts
        width, height = surface.get_size()
        surface.fill(BG)
        bar = round(fonts.normal.get_linesize() * 1.5)
        sdl.draw_rect(surface, PANEL, (0, 0, width, bar))
        pad = round(14 * fonts.scale)
        title = fit(fonts.normal, self.screen.title, width * 3 // 5 - pad)
        surface.blit(fonts.normal.render(title, ACCENT), (pad, (bar - fonts.normal.get_height()) // 2))
        label = fonts.small.render(fit(fonts.small, chat.model_label(), width * 2 // 5 - pad), DIM)
        surface.blit(label, label.get_rect(midright=(width - pad, bar // 2)))
        foot = round(fonts.small.get_linesize() * 1.6)
        sdl.draw_rect(surface, PANEL, (0, height - foot, width, foot))
        surface.blit(fonts.small.render(self.screen.hints, DIM),
                     (pad, height - foot + (foot - fonts.small.get_height()) // 2))
        area = sdl.Rect(pad, bar + pad, width - 2 * pad, height - bar - foot - 2 * pad)
        self.screen.draw(surface, area)

    # Input
    def handle(self, action):
        if action == "menu":
            self.exit_code = 0
        else:
            self.screen.handle(action)

    def action_for(self, event):
        if event.type == sdl.KEYDOWN:
            return {sdl.K_UP: "up", sdl.K_DOWN: "down", sdl.K_LEFT: "left", sdl.K_RIGHT: "right",
                    sdl.K_RETURN: "ok", sdl.K_ESCAPE: "back", sdl.K_x: "x", sdl.K_y: "y",
                    sdl.K_q: "l1", sdl.K_w: "r1", sdl.K_s: "start", sdl.K_m: "menu"}.get(event.key)
        if event.type == sdl.CONTROLLERBUTTONDOWN:
            return {sdl.BUTTON_DPAD_UP: "up", sdl.BUTTON_DPAD_DOWN: "down",
                    sdl.BUTTON_DPAD_LEFT: "left", sdl.BUTTON_DPAD_RIGHT: "right",
                    sdl.BUTTON_A: "ok", sdl.BUTTON_B: "back",
                    sdl.BUTTON_X: "x", sdl.BUTTON_Y: "y",
                    sdl.BUTTON_LEFTSHOULDER: "l1", sdl.BUTTON_RIGHTSHOULDER: "r1",
                    sdl.BUTTON_START: "start", sdl.BUTTON_GUIDE: "menu"}.get(event.button)
        if event.type == sdl.JOYBUTTONDOWN and event.instance_id not in self.controllers:
            return RAW_BUTTONS.get(event.button)
        if event.type == sdl.JOYHATMOTION and event.instance_id not in self.controllers:
            return {(0, 1): "up", (0, -1): "down", (-1, 0): "left", (1, 0): "right"}.get(event.value)
        return None

    def run(self):
        self.controllers = sdl.open_pads()
        repeat_at = shown_at = 0
        dirty = True
        while self.exit_code is None:
            for event in sdl.events():
                if event.type == sdl.QUIT:
                    self.exit_code = 0
                action = self.action_for(event)
                if action and self.exit_code is None:
                    self.handle(action)
                    dirty = True
                    if action in ("up", "down", "left", "right"):
                        self.held, repeat_at = action, time.monotonic() + REPEAT_DELAY
                if event.type in (sdl.KEYUP, sdl.CONTROLLERBUTTONUP, sdl.JOYBUTTONUP) or \
                        event.type == sdl.JOYHATMOTION and event.value == (0, 0):
                    self.held = None
            if self.exit_code is not None:
                break
            if self.held and time.monotonic() >= repeat_at:
                self.handle(self.held)
                repeat_at = time.monotonic() + REPEAT_EVERY
                dirty = True
            dirty |= self.screen.tick()
            # Also redraw twice a second: on the handheld a frame can be lost (the first one after the
            # window opens, or one shown while the screen was off), and nothing else would replace it.
            if dirty or time.monotonic() - shown_at >= REDRAW_EVERY:
                self.draw()
                sdl.flip()
                dirty, shown_at = False, time.monotonic()
            time.sleep(0.03 if self.held else 0.06)
        self.close()
        return self.exit_code

    def close(self):
        """Stop whatever the open screens still run (the model, a download)."""
        for screen in self.stack:
            screen.close()
        self.stack = []


def main():
    size = os.environ.get("BRATTOI_WINDOW")
    surface = sdl.set_mode(tuple(int(v) for v in size.split("x")) if size else None)
    code = App(surface).run()
    sdl.quit()
    return code


if __name__ == "__main__":
    sys.exit(main())
