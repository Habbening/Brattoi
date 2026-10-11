"""Brattoi's logo as sixel pixel art for Mustard Terminal: Brotato (the shiba in the yellow hard hat
whose name gave Brattoi his) with Brattoi riding on top, plus a few faces for animation.

Every frame has the same outline and an opaque inside, so a new frame drawn over an old one
replaces it cleanly (muxterm stacks sixel images and only clears them with the screen).
"""

import os
import shutil

CW, CH = 46, 48  # drawing canvas; W, H below are the cropped logo

COLOURS = {
    "ol": (45, 30, 20), "fur": (215, 135, 60), "furd": (170, 95, 40), "cream": (248, 236, 210),
    "white": (255, 255, 255), "eye": (25, 20, 15), "nose": (20, 18, 18), "mouth": (70, 20, 20),
    "tongue": (235, 110, 120), "hat": (250, 190, 20), "hatl": (255, 225, 110), "hatd": (205, 145, 10),
    "cross": (40, 165, 80), "strap": (40, 40, 45),
    "p_sh": (150, 100, 50), "p": (205, 155, 90), "p_l": (240, 200, 135), "cheek": (235, 120, 110),
}
NAMES = list(COLOURS)
PALETTE = [COLOURS[n] for n in NAMES]

DOG_EYES = (16, 29)   # left column of each 2x3 eye, rows 30-32
POTATO = (23, 7)      # centre of Brattoi on the hat


class Canvas:
    def __init__(self):
        self.px = [[None] * CW for _ in range(CH)]

    def set(self, x, y, c):
        if 0 <= x < CW and 0 <= y < CH:
            self.px[y][x] = c

    def get(self, x, y):
        return self.px[y][x] if 0 <= x < CW and 0 <= y < CH else None

    def ellipse(self, cx, cy, rx, ry, c, test=None):
        for y in range(CH):
            for x in range(CW):
                dx, dy = (x + 0.5 - cx) / rx, (y + 0.5 - cy) / ry
                if dx * dx + dy * dy <= 1 and (test is None or test(x, y)):
                    self.set(x, y, c)

    def outline(self, colour, keep=()):
        """Dark edge on every filled pixel that touches empty space."""
        edge = [(x, y) for y in range(CH) for x in range(CW)
                if self.px[y][x] is not None and self.px[y][x] not in keep and
                any(self.get(x + i, y + j) is None for i, j in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
        for x, y in edge:
            self.set(x, y, colour)


def base():
    """Everything but the eyes."""
    cv = Canvas()
    fur = lambda x, y: cv.get(x, y) == "fur"
    # Head: wide fur oval, darker at the sides.
    cv.ellipse(23, 32, 19, 14, "fur")
    cv.ellipse(23, 32, 19, 14, "furd", test=lambda x, y: fur(x, y) and abs(x + 0.5 - 23) > 15)
    # Shiba mask: cream cheeks and muzzle, orange bridge left between the eyes, eyebrow dots.
    cv.ellipse(14, 37, 8, 7, "cream", test=fur)
    cv.ellipse(32, 37, 8, 7, "cream", test=fur)
    cv.ellipse(23, 39, 8, 6.5, "cream", test=fur)
    cv.ellipse(16, 28.5, 1.6, 1, "cream", test=fur)
    cv.ellipse(30, 28.5, 1.6, 1, "cream", test=fur)
    # Hard hat: dome plus brim, low over the brow, green cross on the front.
    cv.ellipse(23, 27, 17.5, 15, "hat", test=lambda x, y: y < 27)
    for x in range(3, 43):
        cv.set(x, 26, "hatd")
        cv.set(x, 27, "hatd")
    cv.ellipse(15, 17, 5, 3, "hatl", test=lambda x, y: cv.get(x, y) == "hat")
    for i in range(-3, 4):
        for t in (-1, 0, 1):
            cv.set(23 + i, 20 + t, "cross")
            cv.set(23 + t, 20 + i, "cross")
    # Chin straps.
    for y in range(28, 40):
        cv.set(5 + (y - 28) // 3, y, "strap")
        cv.set(40 - (y - 28) // 3, y, "strap")
    # Nose, open grin, tongue.
    cv.ellipse(23, 35, 2.8, 2, "nose")
    cv.set(22, 34, "white")
    cv.ellipse(23, 40, 6, 3.2, "mouth", test=lambda x, y: y >= 38)
    cv.ellipse(23, 42, 3, 2.2, "tongue", test=lambda x, y: y >= 41)
    cv.set(17, 38, "mouth")
    cv.set(29, 38, "mouth")
    # Brattoi riding on the hat, lit from the top left.
    cx, cy, rx, ry = POTATO[0], POTATO[1], 9, 6
    cv.ellipse(cx, cy, rx, ry, "p")
    for y in range(CH):
        for x in range(CW):
            if cv.get(x, y) == "p":
                d = ((x + 0.5 - (cx - rx * 0.4)) ** 2 + (y + 0.5 - (cy - ry * 0.4)) ** 2) ** 0.5
                cv.set(x, y, "p_l" if d < rx * 0.35 else "p" if d < rx * 0.95 else "p_sh")
    for x in range(cx - 2, cx + 2):
        cv.set(x, cy + 2, "mouth")
    cv.set(cx - 5, cy + 1, "cheek")
    cv.set(cx + 4, cy + 1, "cheek")
    cv.outline("ol", keep=("strap", "eye", "nose", "mouth"))
    return cv.px


def crop(grid):
    """Trim empty edges so muxterm's image size matches size_px() exactly."""
    return [row[LEFT:LEFT + W] for row in grid[TOP:TOP + H]]


_FULL = base()
_ROWS = [y for y in range(CH) if any(_FULL[y])]
_COLS = [x for x in range(CW) if any(row[x] for row in _FULL)]
TOP, LEFT = _ROWS[0], _COLS[0]
W, H = _COLS[-1] - LEFT + 1, _ROWS[-1] - TOP + 1
BODY = crop(_FULL)


def face(dog="open", potato="open"):
    """Draw both sets of eyes onto a copy of the base. Returns rows of palette indices."""
    g = [row[:] for row in _FULL]
    for ex in DOG_EYES:
        if dog == "closed":
            for x in range(ex - 1, ex + 3):
                g[31][x] = "eye"
            continue
        x0 = {"left": ex - 1, "right": ex + 1}.get(dog, ex)
        for y in (30, 31, 32):
            g[y][x0] = g[y][x0 + 1] = "eye"
        g[30][x0 + 1] = "white"  # glint
        if dog == "half":
            g[30][x0] = g[30][x0 + 1] = "ol"
    cx, cy = POTATO
    for ex in (cx - 3, cx + 2):
        if potato == "closed":
            g[cy][ex - 1] = g[cy][ex] = "eye"
        else:
            g[cy - 1][ex] = g[cy][ex] = "eye"
    return [[None if c is None else NAMES.index(c) for c in row] for row in crop(g)]


FRAMES = {
    "open": face(),
    "half": face("half"),
    "closed": face("closed"),
    "left": face("left"),
    "right": face("right"),
    "potato": face(potato="closed"),
}

# Brotato blinks, glances each way, Brattoi blinks on his own, then a double blink.
IDLE = [("open", 3.0), ("half", 0.06), ("closed", 0.12), ("half", 0.06),
        ("open", 2.0), ("potato", 0.15), ("open", 1.5), ("left", 0.9), ("open", 0.3), ("right", 0.9),
        ("open", 2.0), ("half", 0.06), ("closed", 0.12), ("half", 0.06), ("open", 0.15),
        ("half", 0.06), ("closed", 0.12), ("half", 0.06), ("open", 1.0), ("potato", 0.15)]


# While the model wakes up or reads the question: looking around, slower than the idle blinks.
THINK = [("left", 0.7), ("open", 0.3), ("right", 0.7), ("open", 0.3), ("half", 0.08), ("closed", 0.15),
         ("half", 0.08), ("open", 0.5), ("potato", 0.2), ("open", 0.5)]


def _read_int(path, default):
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return default


def cell_size():
    """Pixels per text cell: muxterm sizes its grid as screen width // cell width."""
    size = shutil.get_terminal_size((53, 24))
    base_dir = "/opt/muos/device/config/screen/"
    return (_read_int(base_dir + "width", 640) // size.columns,
            _read_int(base_dir + "height", 480) // size.lines)


def size_px(scale):
    """Width and height in pixels as muxterm sees them (height rounded up to a whole sixel band)."""
    return W * scale, -(-H * scale // 6) * 6


def scale_for(rows):
    """The biggest whole-pixel scale that keeps the logo within that many text rows."""
    _, cell_h = cell_size()
    scale = max(1, rows * cell_h // H)
    while scale > 1 and size_px(scale)[1] > rows * cell_h:
        scale -= 1
    return scale


def sixel(grid, scale):
    """Encode a frame as a sixel string; empty pixels stay transparent."""
    w, h = W * scale, H * scale
    pix = [[grid[y // scale][x // scale] for x in range(w)] for y in range(h)]
    out = ["\x1bP0;1q"]
    for n, (r, g, b) in enumerate(PALETTE):
        out.append("#%d;2;%d;%d;%d" % (n, r * 100 // 255, g * 100 // 255, b * 100 // 255))
    for top in range(0, h, 6):
        bands = {}
        for i, row in enumerate(pix[top:top + 6]):
            for x, n in enumerate(row):
                if n is not None:
                    bands.setdefault(n, [0] * w)[x] |= 1 << i
        first = True
        for n, line in sorted(bands.items()):
            out.append(("" if first else "$") + "#%d" % n)
            first = False
            x = 0
            while x < w:
                run = 1
                while x + run < w and line[x + run] == line[x]:
                    run += 1
                ch = chr(63 + line[x])
                if top + 6 >= h and x + run == w and run > 3:
                    # muxterm sizes the image from plain characters only, not "!" repeats, so
                    # the last band must end on one or its rows get cut off.
                    out.append("!%d%s%s" % (run - 1, ch, ch))
                else:
                    out.append("!%d%s" % (run, ch) if run > 3 else ch * run)
                x += run
        out.append("-")
    out[-1] = "\x1b\\"  # the last band needs no newline
    return "".join(out)


def enabled():
    """Mustard Terminal sets IS_MUTERM for the program it runs; plain adb shells get half blocks."""
    return os.environ.get("IS_MUTERM") == "1"
