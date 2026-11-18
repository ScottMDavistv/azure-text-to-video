"""Offline renderer integration acceptance: 300 seconds real MP4, no provider/Azure."""
import os
import pathlib
import sys
import unittest
from uuid import uuid4

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from synthetic_scene_plan import build_scene_plan_from_job
from synthetic_job_renderer import render_scene_plan, SyntheticRenderError


class SyntheticRendererAcceptance(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("SYNTHETIC_RUNTIME_ACCEPTANCE") == "1",
        "Explicit opt-in requires FFmpeg; no Azure or paid services",
    )
    def test_real_plan_renders_valid_three_hundred_second_mp4(self):
        job = {"RowKey": str(uuid4()), "status": "queued",
               "prompt": "Offline five-minute video acceptance"}
        video_bytes = render_scene_plan(build_scene_plan_from_job(job))
        self.assertGreater(len(video_bytes), 1000)
        self.assertLess(len(video_bytes), 100 * 1024 * 1024)
        self.assertEqual(video_bytes[4:8], b"ftyp")

    def test_plan_guard_rejects_wrong_duration_before_spawning_ffmpeg(self):
        job = {"RowKey": str(uuid4()), "status": "queued",
               "prompt": "Offline five-minute video acceptance"}
        plan = build_scene_plan_from_job(job)
        plan["total_duration_seconds"] = 299
        with self.assertRaises(ValueError):
            render_scene_plan(plan)


if __name__ == "__main__":
    unittest.main()
