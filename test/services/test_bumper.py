import os
import tempfile
import unittest
from unittest import mock

from app.services import bumper


class TestBumperNames(unittest.TestCase):
    def test_sanitize_keeps_safe_video_names(self):
        self.assertEqual(bumper.sanitize_bumper_name("encerramento.mp4"), "encerramento.mp4")
        self.assertEqual(bumper.sanitize_bumper_name("C:\\videos\\intro.MOV"), "intro.MOV")
        self.assertEqual(bumper.sanitize_bumper_name("../../a:b.mp4"), "a_b.mp4")

    def test_sanitize_rejects_non_video(self):
        self.assertEqual(bumper.sanitize_bumper_name("script.bat"), "")
        self.assertEqual(bumper.sanitize_bumper_name(""), "")

    def test_resolve_only_inside_bumper_dir(self):
        with tempfile.TemporaryDirectory() as root:
            directory = os.path.join(root, "bumpers")
            os.makedirs(directory)
            open(os.path.join(directory, "intro.mp4"), "wb").close()
            open(os.path.join(root, "secret.mp4"), "wb").close()
            with mock.patch.object(bumper, "bumper_dir", return_value=directory):
                self.assertTrue(bumper.resolve_bumper("intro.mp4"))
                self.assertIsNone(bumper.resolve_bumper("../secret.mp4"))
                self.assertIsNone(bumper.resolve_bumper(os.path.join(root, "secret.mp4")))
                self.assertIsNone(bumper.resolve_bumper("missing.mp4"))
                self.assertEqual(bumper.list_bumpers(), ["intro.mp4"])


class TestFilterGraph(unittest.TestCase):
    main = bumper._MediaInfo(duration=60.0, width=1920, height=1080, fps=30.0, has_audio=True)
    clip = bumper._MediaInfo(duration=12.0, width=1080, height=1920, fps=25.0, has_audio=False)

    def test_end_position_offsets_and_silence(self):
        graph, fade, extra = bumper.build_filter_graph(self.main, self.clip, "end", 1.0)
        self.assertEqual(fade, 1.0)
        self.assertIn("xfade=transition=fade:duration=1.000:offset=59.000", graph)
        self.assertIn("acrossfade=d=1.000", graph)
        self.assertEqual(len(extra), 1)  # bumper without audio gets silence
        self.assertIn("scale=1920:1080:force_original_aspect_ratio=decrease", graph)

    def test_both_positions_split_the_bumper(self):
        graph, _, _ = bumper.build_filter_graph(self.main, self.clip, "both", 1.0)
        self.assertIn("split=2", graph)
        self.assertIn("offset=11.000", graph)   # after the opening clip
        self.assertIn("offset=70.000", graph)   # 12 + 60 - 2 * 1

    def test_crossfade_never_exceeds_half_a_short_clip(self):
        short = bumper._MediaInfo(duration=1.2, width=1920, height=1080, fps=30.0, has_audio=True)
        _, fade, _ = bumper.build_filter_graph(self.main, short, "start", 1.0)
        self.assertAlmostEqual(fade, 0.6)

    def test_off_position_is_a_no_op(self):
        self.assertFalse(bumper.attach_bumper("unused.mp4", "intro.mp4", "none"))


if __name__ == "__main__":
    unittest.main()
