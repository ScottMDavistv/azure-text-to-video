#!/usr/bin/env python3
"""Produce and validate a fully synthetic MP4 without calling any video provider."""
import argparse
import json
import os
import pathlib
import subprocess
import tempfile

COLORS = ("navy", "teal", "maroon", "purple", "darkgreen")
PAID_FLAGS = ("ENABLE_PAID_FAL_WORKER", "ENABLE_LIVE_VIDEO_RENDERING", "ENABLE_WORKER_CONSUMPTION")

def run(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout

def assert_offline():
    for key in PAID_FLAGS:
        if os.getenv(key, "").lower() in ("1", "true", "yes"):
            raise RuntimeError("Refusing synthetic test when paid/worker execution is enabled: " + key)

def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_entries",
                           "format=duration:stream=codec_type,codec_name",
                           "-of", "json", str(path)]))

def validate(probe_data, expected):
    streams = probe_data.get("streams", [])
    types = {stream.get("codec_type") for stream in streams}
    duration = float(probe_data["format"]["duration"])
    if not {"video", "audio"}.issubset(types):
        raise ValueError("Both audio and video required")
    if abs(duration - expected) > 0.6:
        raise ValueError("MP4 duration mismatch: " + str(duration) + " vs " + str(expected))
    return duration

def render(duration=300, segment=60, output_dir="artifacts/nonbillable-render"):
    assert_offline()
    if not isinstance(duration, int) or not 1 <= duration <= 300:
        raise ValueError("Duration must be 1..300 seconds")
    if not isinstance(segment, int) or not 1 <= segment <= 60:
        raise ValueError("Segment duration must be 1..60 seconds")
    dest = pathlib.Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / "synthetic-300s-test.mp4"
    with tempfile.TemporaryDirectory(prefix="synthetic-render-") as temp:
        temp_path = pathlib.Path(temp)
        segments = []
        remaining = duration
        index = 0
        while remaining:
            seconds = min(remaining, segment)
            clip = temp_path / ("scene-%03d.mp4" % index)
            run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 "-f", "lavfi", "-i", "color=c=%s:s=320x180:r=12:d=%s" % (COLORS[index % len(COLORS)], seconds),
                 "-f", "lavfi", "-i", "sine=frequency=%s:sample_rate=16000:duration=%s" % (330 + index * 20, seconds),
                 "-c:v", "libx264", "-preset", "ultrafast", "-crf", "34", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "32k", "-ar", "16000", "-shortest", str(clip)])
            segments.append(clip)
            remaining -= seconds
            index += 1
        playlist = temp_path / "playlist.txt"
        playlist.write_text("".join("file '%s'\n" % p for p in segments), encoding="utf-8")
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-f", "concat", "-safe", "0", "-i", str(playlist),
             "-c", "copy", "-movflags", "+faststart", str(target)])
    details = probe(target)
    actual = validate(details, duration)
    evidence = {
        "type": "synthetic-nonbillable-mp4",
        "target_seconds": duration,
        "measured_seconds": actual,
        "scene_count": len(segments),
        "provider_calls": 0,
        "filesize_bytes": target.stat().st_size,
        "streams": details["streams"],
        "output": str(target),
        "pass": True,
        "limitations": "Synthetic colors and tones only; does not demonstrate text prompt, real provider, subtitles, narration or deployed playback.",
    }
    (dest / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return evidence

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--segment", type=int, default=60)
    parser.add_argument("--out", default="artifacts/nonbillable-render")
    args = parser.parse_args()
    print(json.dumps(render(args.duration, args.segment, args.out), indent=2))
