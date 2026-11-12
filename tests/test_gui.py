"""Screen tests for gui.py, on Linux with SDL's dummy video driver.

Needs BRATTOI_FONT (any .ttf) and SDL2 and SDL2_ttf: installed, or in the folder BRATTOI_SDL_DIR
names. The app is copied to a temporary folder with a stand-in for llama.cpp and empty model files.
"""
import ctypes
import os
import shutil
import struct
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(os.environ.get("BRATTOI_FONT"), "needs BRATTOI_FONT")
class ScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        cls.tmp = tempfile.mkdtemp()
        for name in ("art.py", "ask.py", "chat.py", "gui.py", "models.py", "sdl.py", "models.json"):
            shutil.copy(os.path.join(ROOT, name), cls.tmp)
        shutil.copy(os.path.join(ROOT, "pack", "docs.jsonl"), cls.tmp)
        shutil.copy(os.path.join(ROOT, "tests", "fake_llama.py"), os.path.join(cls.tmp, "llama-completion"))
        os.chmod(os.path.join(cls.tmp, "llama-completion"), 0o755)
        sys.path.insert(0, cls.tmp)
        import gui
        cls.gui = gui
        cls.surface = gui.sdl.set_mode((640, 480))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        for name in os.listdir(self.tmp):
            if name.endswith((".gguf", ".part")) or name in ("model.txt", "controls_seen"):
                os.remove(os.path.join(self.tmp, name))

    def install(self, *files):
        for name in files or self.gui.chat.AUTO_FILES.values():
            open(os.path.join(self.tmp, name), "w").close()

    def app(self, seen=True):
        if seen:
            open(self.gui.chat.CONTROLS_SEEN, "w").close()
        a = self.gui.App(self.surface)
        self.addCleanup(a.close)
        return a

    def names(self, a):
        return [type(s).__name__ for s in a.stack]

    def wait(self, a, done, seconds=10):
        """Run the screen's ticks until done() or time runs out."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            a.screen.tick()
            if done():
                break
            time.sleep(0.01)
        a.draw()
        self.assertTrue(done())

    def test_first_launch_shows_controls_once(self):
        self.install()
        a = self.app(seen=False)
        self.assertEqual(self.names(a), ["Menu", "ControlsScreen"])
        a.draw()
        a.handle("ok")
        self.assertEqual(self.names(a), ["Menu"])
        self.assertEqual(self.names(self.app(seen=False)), ["Menu"])

    def test_no_model_offers_the_model_manager(self):
        a = self.app()
        self.assertEqual(a.screen.title, "Brattoi needs a brain")
        a.draw()
        a.handle("ok")
        self.assertEqual(self.names(a), ["Menu", "ModelsScreen"])
        a.handle("back")
        a.handle("ok")  # Ask muOS, still no model
        self.assertEqual(a.screen.title, "Brattoi needs a brain")

    def test_help_answer_streams_and_shows_sources(self):
        self.install()
        a = self.app()
        self.assertIn("Auto", a.home_items()[4])
        a.handle("ok")
        self.assertEqual(a.screen.title, "Ask muOS")
        for _ in range(4):
            a.handle("down")  # Where do I put BIOS files?
        a.handle("ok")
        talk = a.screen
        self.assertEqual(talk.entries[0], ["You", "Where do I put BIOS files?"])
        self.assertTrue(talk.busy)
        a.draw()
        self.wait(a, lambda: talk.done and not talk.waiting)
        self.assertEqual(talk.entries[1][0], "Brattoi")
        self.assertIn("**BIOS**", talk.entries[1][1])
        self.assertNotIn("[end of text]", talk.entries[1][1])
        self.assertEqual(self.gui.plain(talk.entries[1][1]), "Put BIOS files in MUOS/bios.\n• one\n• two")
        self.assertIn("Source: ", talk.entries[2][1])
        self.assertIn("muos.dev", talk.entries[2][1])
        a.handle("ok")  # a help answer takes no reply
        self.assertEqual(self.names(a)[-1], "Talk")
        a.handle("back")
        self.assertEqual(a.screen.title, "Ask muOS")

    def test_off_topic_question_never_reaches_the_model(self):
        self.install()
        a = self.app()
        a.push(self.gui.Talk(a, "Ask muOS", question="Xbox 360 poem"))
        talk = a.screen
        self.assertFalse(talk.waiting)
        self.assertIn("not in the muOS docs", talk.entries[1][1])
        a.draw()

    def test_chat_turns_then_memory_full(self):
        self.install()
        a = self.app()
        a.screen.selected = 3  # Free chat
        a.handle("ok")
        talk = a.screen
        self.assertEqual(talk.busy, "Waking Brattoi up...")
        a.handle("ok")  # not yet
        self.assertEqual(self.names(a)[-1], "Talk")
        self.wait(a, lambda: not talk.waiting)
        for turn in (1, 2):
            a.handle("x")
            keys = a.screen
            self.assertEqual(type(keys).__name__, "KeyboardScreen")
            keys.text = "hello %d" % turn
            a.draw()
            a.handle("start")
            self.assertIs(a.screen, talk)
            self.wait(a, lambda: not talk.waiting)
            self.assertEqual(talk.entries[-2:], [["You", "hello %d" % turn],
                                                 ["Brattoi", "Reply %d to: hello %d" % (turn, turn)]])
        talk.send("three")
        self.wait(a, lambda: talk.done)
        self.assertEqual(talk.entries[-1], ["", "Brattoi's memory is full. Start a new chat."])
        a.handle("ok")
        self.assertIs(a.screen, talk)

    def test_fun_prompt_is_sent_first_and_back_stops_the_model(self):
        self.install()
        a = self.app()
        a.screen.selected = 1
        a.handle("ok")
        a.handle("ok")
        talk = a.screen
        self.wait(a, lambda: talk.turns == 1)
        self.assertEqual(talk.entries[0], ["You", self.gui.chat.FUN_PROMPTS[0]])
        a.handle("back")
        self.assertEqual(a.screen.title, "Just for fun")
        talk.chat.proc.wait(timeout=5)

    def test_typed_question_and_keyboard(self):
        self.install()
        a = self.app()
        a.screen.selected = 2
        a.handle("ok")
        keys = a.screen
        keys.row, keys.col = 1, 0
        a.handle("ok")
        a.handle("x")
        a.handle("ok")
        a.handle("y")
        self.assertEqual(keys.text, "qQ ")
        a.handle("back")
        self.assertEqual(keys.text, "qQ")
        keys.text = "Where do I put BIOS files?"
        a.handle("start")
        self.assertEqual(self.names(a), ["Menu", "Talk"])

    def test_models_use_and_delete(self):
        self.install()
        a = self.app()
        chat = self.gui.chat
        self.assertTrue(chat.AUTO)
        a.screen.selected = 4
        a.handle("ok")
        screen = a.screen
        rows = screen.rows()
        self.assertEqual(rows[0][2], "Active")
        self.assertEqual([r[2] for r in rows[1:3]], ["Auto", "Auto"])
        self.assertTrue(rows[3][2].startswith("Download "))
        a.draw()
        a.handle("down")  # Granite
        a.handle("ok")
        self.assertEqual(a.screen.labels(), ["Use", "Delete"])
        a.handle("ok")
        self.assertFalse(chat.AUTO)
        self.assertTrue(self.gui.ask.MODEL.endswith("granite-4.0-350m-Q4_0.gguf"))
        self.assertIs(a.screen, screen)
        a.handle("ok")
        self.assertEqual(a.screen.labels(), ["Delete"])
        a.handle("ok")
        self.assertEqual(a.screen.labels(), ["No", "Yes, delete it"])
        a.draw()
        a.handle("down")
        a.handle("ok")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "granite-4.0-350m-Q4_0.gguf")))
        self.assertTrue(self.gui.ask.MODEL.endswith("Qwen2.5-0.5B-Instruct-Q4_0.gguf"))
        a.handle("down")
        a.handle("down")  # LFM2, not installed
        a.handle("ok")
        self.assertTrue(a.screen.labels()[0].startswith("Download ("))

    def test_download_progress_cancel_and_result(self):
        self.install()
        a = self.app()
        gui = self.gui
        entry = gui.models.catalogue()[2]

        def download(got, progress, cancelled):
            progress(50, 100, 2e6)
            while not cancelled():
                time.sleep(0.01)
            return "cancelled"
        real, gui.models.download = gui.models.download, download
        self.addCleanup(setattr, gui.models, "download", real)
        a.push(gui.DownloadScreen(a, entry))
        screen = a.screen
        self.wait(a, lambda: screen.progress[0] == 50)
        self.assertEqual(screen.hints, "B: cancel")
        a.handle("back")
        self.wait(a, lambda: screen.result == "cancelled")
        self.assertIs(a.screen, screen)
        a.handle("ok")
        self.assertIsNot(a.screen, screen)

    def test_menu_button_quits_and_buttons_map(self):
        self.install()
        a = self.app()
        sdl = self.gui.sdl
        a.controllers = {7}
        self.assertEqual(a.action_for(sdl.Event(sdl.CONTROLLERBUTTONDOWN, button=sdl.BUTTON_A, instance_id=7)), "ok")
        self.assertEqual(a.action_for(sdl.Event(sdl.JOYBUTTONDOWN, button=3, instance_id=1)), "ok")
        self.assertIsNone(a.action_for(sdl.Event(sdl.JOYBUTTONDOWN, button=3, instance_id=7)))
        self.assertEqual(a.action_for(sdl.Event(sdl.JOYHATMOTION, value=(0, -1), instance_id=1)), "down")
        a.handle("menu")
        self.assertEqual(a.exit_code, 0)
        b = self.app()
        b.handle("back")  # B on the first screen
        self.assertEqual(b.exit_code, 0)

    def test_think_blocks_are_left_out(self):
        ask = self.gui.ask

        class Got:
            text = ""

            def write(self, part):
                self.text += part

        for chunks, want in (
                (["<think>", "\n\n</think>\n\nHello"], "Hello"),
                (["<thi", "nk>plan plan</th", "ink>\nHi <b> there"], "Hi <b> there"),
                (["a < b and <thin", "g>"], "a < b and <thing>"),
                (["No block.\nSecond line"], "No block.\nSecond line")):
            got = Got()
            stream = ask.NoThink(got)
            for chunk in chunks:
                stream.write(chunk)
            self.assertEqual(got.text, want)

    def test_logo_is_drawn(self):
        self.install()
        a = self.app()
        a.draw()
        raw = self.surface.raw.contents
        top = ctypes.string_at(raw.pixels, raw.pitch * 150)
        seen = {p & 0xFFFFFF for p in struct.unpack("<%dI" % (len(top) // 4), top)}
        hat = self.gui.art.COLOURS["hat"]
        self.assertIn(hat[0] << 16 | hat[1] << 8 | hat[2], seen)
        self.assertTrue(a.screen.faces)


if __name__ == "__main__":
    unittest.main()
