import importlib.util
import pathlib
import unittest
from unittest.mock import patch

PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "synthetic_300s_render.py"
spec = importlib.util.spec_from_file_location("synthetic_300s_render", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class SyntheticVideoTests(unittest.TestCase):
    def test_valid_probe(self):
        data = {"streams":[{"codec_type":"video"},{"codec_type":"audio"}],
                "format":{"duration":"300.10"}}
        self.assertAlmostEqual(mod.validate(data, 300),300.1)
    def test_missing_audio_rejected(self):
        with self.assertRaises(ValueError):
            mod.validate({"streams":[{"codec_type":"video"}],"format":{"duration":"300"}},300)
    def test_short_duration_rejected(self):
        with self.assertRaises(ValueError):
            mod.validate({"streams":[{"codec_type":"video"},{"codec_type":"audio"}],
                          "format":{"duration":"299.0"}},300)
    def test_paid_flags_rejected(self):
        with patch.dict("os.environ", {"ENABLE_PAID_FAL_WORKER":"true"}):
            with self.assertRaises(RuntimeError):
                mod.assert_offline()
    def test_validate_input_before_ffmpeg(self):
        with self.assertRaises(ValueError):
            mod.render(duration=301)
