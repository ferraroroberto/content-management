"""Pure tests for the demo-video storyboard (issue #359) — no node, no ffmpeg."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError  # noqa: E402

import demo_video_pipeline  # noqa: E402
from demo_video.storyboard import Demo, load_demo, missing_media, resolve_cut, total_frames, validate  # noqa: E402

EXAMPLE = REPO_ROOT / "demo_video" / "examples" / "facilitation-suite"


def _raw() -> dict:
    return json.loads((EXAMPLE / "demo.json").read_text(encoding="utf-8"))


class StoryboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(EXAMPLE / "marks.json", self.tmp / "marks.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, raw: dict) -> Demo:
        (self.tmp / "demo.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        return load_demo(self.tmp)

    def test_the_example_is_valid_and_83_seconds(self) -> None:
        demo = self._write(_raw())
        self.assertEqual(validate(demo, self.tmp), [])
        self.assertEqual(total_frames(demo), 83 * 30)

    def test_an_unknown_scene_type_is_rejected(self) -> None:
        raw = _raw()
        raw["scenes"][2] = {"id": "x", "type": "fireworks", "seconds": 3}
        with self.assertRaises(ValidationError):
            self._write(raw)

    def test_a_copy_key_missing_for_a_cut_language_is_reported(self) -> None:
        raw = _raw()
        del raw["copy"]["en"]["brandSub"]
        errors = validate(self._write(raw), self.tmp)
        self.assertTrue(any("'@brandSub'" in e and "'en'" in e for e in errors), errors)

    def test_a_cut_language_without_copy_is_reported(self) -> None:
        raw = _raw()
        raw["cuts"].append({"id": "fr", "lang": "fr"})
        errors = validate(self._write(raw), self.tmp)
        self.assertIn("cut fr: no copy for language 'fr'", errors)

    def test_a_clip_ref_to_an_unknown_beat_is_reported(self) -> None:
        raw = _raw()
        raw["scenes"][0]["clip"]["beat"] = "no_such_beat"
        errors = validate(self._write(raw), self.tmp)
        self.assertTrue(any("beat 'no_such_beat'" in e for e in errors), errors)

    def test_a_clip_ref_to_an_unknown_video_is_reported(self) -> None:
        raw = _raw()
        raw["scenes"][0]["clip"]["video"] = "webcam"
        errors = validate(self._write(raw), self.tmp)
        self.assertTrue(any("unknown video 'webcam'" in e for e in errors), errors)

    def test_resolve_turns_copy_keys_and_clip_refs_into_props(self) -> None:
        demo = self._write(_raw())
        props = resolve_cut(demo, self.tmp, demo.cut("en-linkedin"))
        marks = json.loads((EXAMPLE / "marks.json").read_text(encoding="utf-8"))
        self.assertEqual((props["width"], props["height"], props["durationInFrames"]), (1920, 1080, 2490))
        hook = props["scenes"][0]
        self.assertEqual(hook["caption"]["title"], demo.copy_["en"]["hook"])
        self.assertEqual(hook["clip"], {"src": "en/stage.mp4", "from": round(marks["stage"]["map"][0] + 2.0, 3),
                                        "rate": 1.45})
        # chat senders follow the recorder's people[(n*7) % len] rule
        people = demo.copy_["en"]["people"]
        self.assertEqual(hook["chat"]["messages"][0], {"name": people[7 % len(people)], "text": "London, UK"})
        steps = next(s for s in props["scenes"] if s["type"] == "steps_device")
        self.assertEqual(steps["step_at"], [round(marks["plan"]["plan"][0] + 1.5, 3), round(marks["plan"]["groups"][0], 3)])
        states = next(s for s in props["scenes"] if s["type"] == "window_states")
        self.assertEqual(states["legend"]["anchor"], round(marks["stage"]["breakout"][0], 3))
        self.assertIsNone(states["segments"][1]["seconds"])
        self.assertEqual([s["from"] for s in props["scenes"]][:3], [0, 210, 390])
        self.assertEqual(props["tracks"][0]["dur"], 2490)

    def test_aspect_presets_set_the_canvas(self) -> None:
        demo = self._write(_raw())
        self.assertEqual(resolve_cut(demo, self.tmp, demo.cut("en-square"))["height"], 1080)
        self.assertEqual(resolve_cut(demo, self.tmp, demo.cut("en-portrait"))["height"], 1350)

    def test_a_crossfaded_track_is_trimmed_to_end_with_the_video(self) -> None:
        raw = _raw()
        raw["cuts"][0]["soundtrack"] = [
            {"file": "music/a.mp3", "licence": "free", "end": {"scene": "groups", "offset_s": 1.5}, "fade_out_s": 3},
            {"file": "music/b.mp3", "licence": "free", "start": {"scene": "groups", "offset_s": -1.5},
             "align_end_tail_s": 1.0, "fade_in_s": 2},
        ]
        demo = self._write(raw)
        self.assertEqual(validate(demo, self.tmp), [])
        a, b = resolve_cut(demo, self.tmp, demo.cut("en-linkedin"), track_seconds={"music/b.mp3": 60.0})["tracks"]
        groups = 1350  # frame the groups scene starts at
        self.assertEqual((a["from"], a["dur"], a["fade_out"]), (0, groups + 45, 90))
        self.assertEqual((b["from"], b["dur"]), (groups - 45, 2490 - groups + 45))
        self.assertEqual(b["start_from"], 59 * 30 - b["dur"])  # its last second is cut, then it ends with the video
        with self.assertRaises(ValueError):
            resolve_cut(demo, self.tmp, demo.cut("en-linkedin"))  # no length for the aligned track

    def test_missing_media_lists_clips_and_tracks(self) -> None:
        demo = self._write(_raw())
        missing = missing_media(self.tmp, demo, demo.cut("en-linkedin"))
        self.assertEqual(sorted(missing), ["en/plan.mp4", "en/presenter.mp4", "en/results.mp4", "en/stage.mp4",
                                           "music/en.mp3"])


class PipelineStatusTests(unittest.TestCase):
    def test_status_writes_nothing_and_exits_2_on_missing_media(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            for f in ("demo.json", "marks.json"):
                shutil.copy(EXAMPLE / f, tmp / f)
            before = sorted(p.name for p in tmp.rglob("*"))
            self.assertEqual(demo_video_pipeline.main([str(tmp), "--status"]), 2)
            self.assertEqual(sorted(p.name for p in tmp.rglob("*")), before)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_an_invalid_storyboard_exits_2(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            raw = copy.deepcopy(_raw())
            raw["cuts"] = []
            (tmp / "demo.json").write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual(demo_video_pipeline.main([str(tmp), "--status"]), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
