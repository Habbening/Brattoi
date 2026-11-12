"""The SDL2 and SDL2_ttf calls Brattoi's screens need, through ctypes.

The libraries are the device's own: the ones muOS and its terminal draw with. Nothing is bundled.
BRATTOI_SDL_DIR points at another folder holding them (the tests use it).
"""
import ctypes
import glob
import math
import os
import struct
from ctypes import POINTER, Structure, byref, c_char_p, c_int, c_uint8, c_uint32, c_void_p

INIT_VIDEO, INIT_JOYSTICK, INIT_GAMECONTROLLER = 0x20, 0x200, 0x2000
WINDOW_FULLSCREEN_DESKTOP = 0x1001
WINDOWPOS_UNDEFINED = 0x1FFF0000
PIXELFORMAT_ARGB8888 = 0x16362004
TEXTUREACCESS_STREAMING = 1

QUIT, KEYDOWN, KEYUP = 0x100, 0x300, 0x301
JOYHATMOTION, JOYBUTTONDOWN, JOYBUTTONUP = 0x602, 0x603, 0x604
CONTROLLERBUTTONDOWN, CONTROLLERBUTTONUP = 0x651, 0x652

K_RETURN, K_ESCAPE = 13, 27
K_m, K_q, K_s, K_w, K_x, K_y = (ord(c) for c in "mqswxy")
K_RIGHT, K_LEFT, K_DOWN, K_UP = 0x4000004F, 0x40000050, 0x40000051, 0x40000052

(BUTTON_A, BUTTON_B, BUTTON_X, BUTTON_Y, BUTTON_BACK, BUTTON_GUIDE, BUTTON_START, BUTTON_LEFTSTICK,
 BUTTON_RIGHTSTICK, BUTTON_LEFTSHOULDER, BUTTON_RIGHTSHOULDER, BUTTON_DPAD_UP, BUTTON_DPAD_DOWN,
 BUTTON_DPAD_LEFT, BUTTON_DPAD_RIGHT) = range(15)


class _Rect(Structure):
    _fields_ = [("x", c_int), ("y", c_int), ("w", c_int), ("h", c_int)]


class _Colour(Structure):
    _fields_ = [("r", c_uint8), ("g", c_uint8), ("b", c_uint8), ("a", c_uint8)]


class _Surface(Structure):
    # The start of SDL_Surface. The rest is not read.
    _fields_ = [("flags", c_uint32), ("format", c_void_p), ("w", c_int), ("h", c_int), ("pitch", c_int),
                ("pixels", c_void_p)]


_SurfaceP = POINTER(_Surface)
_RectP = POINTER(_Rect)
_IntP = POINTER(c_int)

_SDL_CALLS = {
    "SDL_Init": (c_int, c_uint32),
    "SDL_Quit": (None,),
    "SDL_GetError": (c_char_p,),
    "SDL_CreateWindow": (c_void_p, c_char_p, c_int, c_int, c_int, c_int, c_uint32),
    "SDL_DestroyWindow": (None, c_void_p),
    "SDL_CreateRenderer": (c_void_p, c_void_p, c_int, c_uint32),
    "SDL_DestroyRenderer": (None, c_void_p),
    "SDL_GetRendererOutputSize": (c_int, c_void_p, _IntP, _IntP),
    "SDL_CreateTexture": (c_void_p, c_void_p, c_uint32, c_int, c_int, c_int),
    "SDL_UpdateTexture": (c_int, c_void_p, _RectP, c_void_p, c_int),
    "SDL_RenderClear": (c_int, c_void_p),
    "SDL_RenderCopy": (c_int, c_void_p, c_void_p, _RectP, _RectP),
    "SDL_RenderPresent": (None, c_void_p),
    "SDL_CreateRGBSurfaceWithFormat": (_SurfaceP, c_uint32, c_int, c_int, c_int, c_uint32),
    "SDL_FreeSurface": (None, _SurfaceP),
    "SDL_FillRect": (c_int, _SurfaceP, _RectP, c_uint32),
    "SDL_UpperBlit": (c_int, _SurfaceP, _RectP, _SurfaceP, _RectP),
    "SDL_SetClipRect": (c_int, _SurfaceP, _RectP),
    "SDL_GetClipRect": (None, _SurfaceP, _RectP),
    "SDL_ShowCursor": (c_int, c_int),
    "SDL_PollEvent": (c_int, c_void_p),
    "SDL_PumpEvents": (None,),
    "SDL_FlushEvents": (None, c_uint32, c_uint32),
    "SDL_NumJoysticks": (c_int,),
    "SDL_IsGameController": (c_int, c_int),
    "SDL_GameControllerOpen": (c_void_p, c_int),
    "SDL_GameControllerGetJoystick": (c_void_p, c_void_p),
    "SDL_JoystickOpen": (c_void_p, c_int),
    "SDL_JoystickInstanceID": (c_int, c_void_p),
}
_TTF_CALLS = {
    "TTF_Init": (c_int,),
    "TTF_OpenFont": (c_void_p, c_char_p, c_int),
    "TTF_SizeUTF8": (c_int, c_void_p, c_char_p, _IntP, _IntP),
    "TTF_RenderUTF8_Blended": (_SurfaceP, c_void_p, c_char_p, _Colour),
    "TTF_FontHeight": (c_int, c_void_p),
    "TTF_FontLineSkip": (c_int, c_void_p),
}

_sdl = _ttf = None
_window = _renderer = _texture = _canvas = None
_pads = []  # kept open for as long as the program runs


def _load(soname, pattern, calls):
    folder = os.environ.get("BRATTOI_SDL_DIR")
    found = sorted(glob.glob(os.path.join(folder, pattern))) if folder else []
    lib = ctypes.CDLL(found[0] if found else soname)
    for name, (restype, *argtypes) in calls.items():
        call = getattr(lib, name)
        call.restype, call.argtypes = restype, argtypes
    return lib


def _fail(what):
    raise SystemExit(f"Brattoi: {what}: {_sdl.SDL_GetError().decode('utf-8', 'replace')}")


def init():
    global _sdl, _ttf
    if _sdl:
        return
    _sdl = _load("libSDL2-2.0.so.0", "libSDL2-2*", _SDL_CALLS)
    _ttf = _load("libSDL2_ttf-2.0.so.0", "libSDL2_ttf-*", _TTF_CALLS)
    if _sdl.SDL_Init(INIT_VIDEO | INIT_JOYSTICK | INIT_GAMECONTROLLER):
        _fail("SDL_Init")
    if _ttf.TTF_Init():
        _fail("TTF_Init")


def quit():
    if _sdl:
        _sdl.SDL_Quit()


def _bytes(text):
    return text.encode("utf-8", "replace")


def _pixel(colour):
    return 0xFF000000 | colour[0] << 16 | colour[1] << 8 | colour[2]


class Rect:
    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = int(x), int(y), int(width), int(height)

    def __iter__(self):
        return iter((self.x, self.y, self.width, self.height))

    left = property(lambda self: self.x)
    right = property(lambda self: self.x + self.width)
    bottom = property(lambda self: self.y + self.height)
    centery = property(lambda self: self.y + self.height // 2)
    center = property(lambda self: (self.x + self.width // 2, self.y + self.height // 2))
    topleft = property(lambda self: (self.x, self.y))

    def inflate(self, dx, dy):
        return Rect(self.x - dx // 2, self.y - dy // 2, self.width + dx, self.height + dy)


class Surface:
    """A picture in memory: the screen canvas, or one rendered line of text."""

    def __init__(self, raw, width=0, height=0):
        self.raw = raw  # None: nothing to draw (empty text)
        self.width, self.height = (raw.contents.w, raw.contents.h) if raw else (width, height)

    def __del__(self):
        if self.raw and _sdl:
            _sdl.SDL_FreeSurface(self.raw)

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height

    def get_size(self):
        return self.width, self.height

    def get_rect(self, topright=None, midright=None, center=None):
        """This picture's rectangle, placed by one of its points."""
        if topright:
            return Rect(topright[0] - self.width, topright[1], self.width, self.height)
        if midright:
            return Rect(midright[0] - self.width, midright[1] - self.height // 2, self.width, self.height)
        if center:
            return Rect(center[0] - self.width // 2, center[1] - self.height // 2, self.width, self.height)
        return Rect(0, 0, self.width, self.height)

    def fill(self, colour):
        _sdl.SDL_FillRect(self.raw, None, _pixel(colour))

    def blit(self, image, where):
        """Draw image with its top left corner at where (a point or a Rect)."""
        if image.raw:
            x, y = tuple(where)[:2]
            _sdl.SDL_UpperBlit(image.raw, None, self.raw, byref(_Rect(int(x), int(y), 0, 0)))

    def get_clip(self):
        rect = _Rect()
        _sdl.SDL_GetClipRect(self.raw, byref(rect))
        return Rect(rect.x, rect.y, rect.w, rect.h)

    def set_clip(self, rect):
        _sdl.SDL_SetClipRect(self.raw, byref(_Rect(*rect)))


def blank(width, height):
    """An empty see-through picture to draw on."""
    return Surface(_sdl.SDL_CreateRGBSurfaceWithFormat(0, width, height, 32, PIXELFORMAT_ARGB8888))


class Font:
    def __init__(self, path, size):
        self.raw = _ttf.TTF_OpenFont(os.fsencode(path), size)
        if not self.raw:
            _fail(f"font {path}")

    def size(self, text):
        width, height = c_int(), c_int()
        _ttf.TTF_SizeUTF8(self.raw, _bytes(text), byref(width), byref(height))
        return width.value, height.value

    def render(self, text, colour):
        raw = _ttf.TTF_RenderUTF8_Blended(self.raw, _bytes(text), _Colour(*colour, 255)) if text else None
        return Surface(raw, 0, self.get_height())

    def get_height(self):
        return _ttf.TTF_FontHeight(self.raw)

    def get_linesize(self):
        return _ttf.TTF_FontLineSkip(self.raw)


def _fill(surface, pixel, x, y, width, height):
    if width > 0 and height > 0:
        _sdl.SDL_FillRect(surface.raw, byref(_Rect(x, y, width, height)), pixel)


def draw_rect(surface, colour, rect, border_radius=0):
    x, y, width, height = (int(v) for v in rect)
    pixel = _pixel(colour)
    radius = max(0, min(border_radius, width // 2, height // 2))
    _fill(surface, pixel, x, y + radius, width, height - 2 * radius)
    for row in range(radius):
        # How far this row of the corner sits in from the edge.
        inset = radius - round(math.sqrt(radius * radius - (radius - row - 0.5) ** 2))
        _fill(surface, pixel, x + inset, y + row, width - 2 * inset, 1)
        _fill(surface, pixel, x + inset, y + height - 1 - row, width - 2 * inset, 1)


def draw_circle(surface, colour, centre, radius):
    pixel = _pixel(colour)
    for dy in range(-radius, radius + 1):
        half = int(math.sqrt(radius * radius - dy * dy))
        _fill(surface, pixel, centre[0] - half, centre[1] + dy, 2 * half + 1, 1)


def set_mode(size=None):
    """Open the window, the whole screen when size is None. Returns the canvas the screens draw on."""
    global _window, _renderer, _texture, _canvas
    init()
    if _window:
        _sdl.SDL_DestroyRenderer(_renderer)  # frees the texture too
        _sdl.SDL_DestroyWindow(_window)
    width, height = size or (0, 0)
    _window = _sdl.SDL_CreateWindow(b"Brattoi", WINDOWPOS_UNDEFINED, WINDOWPOS_UNDEFINED, width, height,
                                    0 if size else WINDOW_FULLSCREEN_DESKTOP)
    if not _window:
        _fail("SDL_CreateWindow")
    _renderer = _sdl.SDL_CreateRenderer(_window, -1, 0)
    if not _renderer:
        _fail("SDL_CreateRenderer")
    out_w, out_h = c_int(), c_int()
    if _sdl.SDL_GetRendererOutputSize(_renderer, byref(out_w), byref(out_h)):
        _fail("SDL_GetRendererOutputSize")
    _texture = _sdl.SDL_CreateTexture(_renderer, PIXELFORMAT_ARGB8888, TEXTUREACCESS_STREAMING, out_w.value, out_h.value)
    raw = _sdl.SDL_CreateRGBSurfaceWithFormat(0, out_w.value, out_h.value, 32, PIXELFORMAT_ARGB8888)
    if not _texture or not raw:
        _fail("screen canvas")
    _sdl.SDL_ShowCursor(0)
    _canvas = Surface(raw)
    return _canvas


def flip():
    """Show the canvas."""
    _sdl.SDL_UpdateTexture(_texture, None, _canvas.raw.contents.pixels, _canvas.raw.contents.pitch)
    _sdl.SDL_RenderClear(_renderer)
    _sdl.SDL_RenderCopy(_renderer, _texture, None, None)
    _sdl.SDL_RenderPresent(_renderer)


def open_pads():
    """Open every pad. Returns the instance ids of the ones SDL has a controller mapping for."""
    mapped = set()
    for index in range(_sdl.SDL_NumJoysticks()):
        if _sdl.SDL_IsGameController(index):
            pad = _sdl.SDL_GameControllerOpen(index)
            if pad:
                mapped.add(_sdl.SDL_JoystickInstanceID(_sdl.SDL_GameControllerGetJoystick(pad)))
        else:
            pad = _sdl.SDL_JoystickOpen(index)
        _pads.append(pad)
    return mapped


class Event:
    def __init__(self, type, **fields):
        self.type = type
        self.__dict__.update(fields)


def _event(raw):
    """One SDL_Event as an Event, or None for kinds the screens do not use."""
    kind = struct.unpack_from("<I", raw)[0]
    if kind == QUIT:
        return Event(kind)
    if kind in (KEYDOWN, KEYUP):
        repeat, key = struct.unpack_from("<B6xi", raw, 13)
        # Held keys repeat in the app's own time, like the buttons.
        return None if repeat else Event(kind, key=key)
    if kind in (JOYBUTTONDOWN, JOYBUTTONUP, CONTROLLERBUTTONDOWN, CONTROLLERBUTTONUP):
        instance_id, button = struct.unpack_from("<iB", raw, 8)
        return Event(kind, instance_id=instance_id, button=button)
    if kind == JOYHATMOTION:
        instance_id, _, bits = struct.unpack_from("<iBB", raw, 8)
        # SDL: up 1, right 2, down 4, left 8. Here: (x, y), right and up positive.
        value = (bool(bits & 2) - bool(bits & 8), bool(bits & 1) - bool(bits & 4))
        return Event(kind, instance_id=instance_id, value=value)
    return None


def events():
    """The events waiting since the last call."""
    found = []
    raw = ctypes.create_string_buffer(64)  # SDL_Event is 56 bytes
    while _sdl.SDL_PollEvent(raw):
        event = _event(raw)
        if event:
            found.append(event)
    return found


def clear_events():
    _sdl.SDL_PumpEvents()
    _sdl.SDL_FlushEvents(0, 0xFFFF)
