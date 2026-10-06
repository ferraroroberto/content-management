"""Tests for the demo-video checks and media prep (issue #360). The ffmpeg ones skip without ffmpeg."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo_video import checks, media  # noqa: E402
from demo_video.storyboard import Demo, load_demo  # noqa: E402
from podcast.media import NO_WINDOW  # noqa: E402

EXAMPLE = REPO_ROOT / "demo_video" / "examples" / "facilitation-suite"
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _raw() -> dict:
    return json.loads((EXAMPLE / "demo.json").read_text(encoding="utf-8"))


class _Folder(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def demo(self, raw: dict, marks: dict | None = None) -> Demo:
        (self.tmp / "demo.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        (self.tmp / "marks.json").write_text(json.dumps(marks if marks is not None else
                                                        json.loads((EXAMPLE / "marks.json").read_text())), encoding="utf-8")
        return load_demo(self.tmp)


class OverrunTests(_Folder):
    def _hook_only(self, rate: float) -> Demo:
        raw = _raw()
        hook = copy.deepcopy(raw["scenes"][0])
        hook.update(seconds=8, clip={"video": "stage", "beat": "answers", "offset": 2.3, "rate": rate})
        raw["scenes"] = [hook]
        return self.demo(raw, {"stage": {"answers": [10.0, 22.0]}})  # a 12 s beat

    def test_a_sped_up_clip_past_its_beat_fails_naming_the_beat_and_scene(self) -> None:
        demo = self._hook_only(1.6)  # 2.3 + 8 × 1.6 = 15.1 s > 12 s
        r = checks.overrun(demo, self.tmp, demo.cut("en-linkedin"))
        self.assertEqual(r.state, "fail")
        self.assertIn("hook.clip", r.reason)
        self.assertIn("beat 'answers'", r.reason)
        self.assertIn("15.1 s", r.reason)

    def test_a_clip_inside_its_beat_passes(self) -> None:
        demo = self._hook_only(1.0)  # 2.3 + 8 = 10.3 s ≤ 12 s
        self.assertEqual(checks.overrun(demo, self.tmp, demo.cut("en-linkedin")).state, "pass")

    def test_a_clip_without_a_beat_and_no_media_is_unknown_not_pass(self) -> None:
        raw = _raw()
        raw["scenes"] = [s for s in raw["scenes"] if s["id"] == "results"]  # clip has no beat
        demo = self.demo(raw)
        r = checks.overrun(demo, self.tmp, demo.cut("en-linkedin"))
        self.assertEqual(r.state, "unknown")
        self.assertFalse(r.ok)

    def test_window_states_segments_are_checked_for_their_own_length(self) -> None:
        raw = _raw()
        groups = next(s for s in raw["scenes"] if s["id"] == "groups")
        groups["segments"][1]["clip"]["rate"] = 4.0  # 7.5 s × 4 runs far past the breakout beat
        raw["scenes"] = [groups]
        groups["legend"]["changes"] = [[0, 0]]
        demo = self.demo(raw)
        r = checks.overrun(demo, self.tmp, demo.cut("en-linkedin"))
        self.assertEqual(r.state, "fail")
        self.assertIn("groups.segments[1]", r.reason)


class PrivacyTests(_Folder):
    def _with_people(self, people: list[str], **privacy) -> Demo:
        raw = _raw()
        raw["copy"]["en"]["people"] = people
        raw["privacy"] = privacy
        return self.demo(raw)

    def test_a_made_up_name_sharing_a_real_surname_fails(self) -> None:
        demo = self._with_people(["Andrés Navarro", "Maya Collins"])
        r = checks.privacy(demo, self.tmp, roster=["Ana Navarro"])
        self.assertEqual(r.state, "fail")
        self.assertIn("navarro", r.reason)

    def test_a_made_up_name_sharing_nothing_passes(self) -> None:
        demo = self._with_people(["Andrés Soler", "Maya Collins"])
        self.assertEqual(checks.privacy(demo, self.tmp, roster=["Ana Navarro"]).state, "pass")

    def test_accents_and_case_do_not_hide_a_match(self) -> None:
        demo = self._with_people(["ANDRES SOLER"])
        self.assertEqual(checks.privacy(demo, self.tmp, roster=["Andrés Soler"]).state, "fail")

    def test_an_allowed_token_is_accepted(self) -> None:
        demo = self._with_people(["Javier Pastor"], allow=["Javier"])
        self.assertEqual(checks.privacy(demo, self.tmp, roster=["Javier Quillfeather"]).state, "pass")

    def test_a_roster_file_is_read_and_a_missing_one_is_unknown(self) -> None:
        (self.tmp / "roster.txt").write_text("# header\nAna Navarro\n", encoding="utf-8")
        self.assertEqual(checks.privacy(self._with_people(["Andrés Navarro"], roster="roster.txt"), self.tmp).state, "fail")
        self.assertEqual(checks.privacy(self._with_people(["Andrés Soler"], roster="nope.txt"), self.tmp).state, "unknown")

    def test_a_blocklisted_term_fails_only_a_public_cut(self) -> None:
        raw = _raw()
        raw["copy"]["en"]["footer"] = "A workshop for Example Client Ltd"
        raw["privacy"] = {"blocklist": ["Example Client Ltd"]}
        self.assertEqual(checks.privacy(self.demo(raw), self.tmp).state, "fail")
        for cut in raw["cuts"]:
            cut["public"] = False
            cut["soundtrack"] = []
        self.assertEqual(checks.privacy(self.demo(raw), self.tmp).state, "pass")


class LicenceTests(_Folder):
    def test_private_only_music_fails_a_public_cut_and_passes_a_private_one(self) -> None:
        raw = _raw()
        raw["cuts"] = [{"id": "c", "lang": "en", "public": True,
                        "soundtrack": [{"file": "music/x.mp3", "licence": "private-only"}]}]
        self.assertEqual(checks.licence(self.demo(raw)).state, "fail")
        raw["cuts"][0]["public"] = False
        self.assertEqual(checks.licence(self.demo(raw)).state, "pass")


class SwapTests(unittest.TestCase):
    def test_the_boundary_where_the_closing_track_builds_ranks_first(self) -> None:
        env = [-30.0] * 20 + [-10.0] * 20  # a 40 s track that builds at 20 s
        ranked = media.suggest_swaps(env, track_end_s=39, video_s=40, boundaries=[("calm", 10.0), ("build", 21.0)])
        self.assertEqual(ranked[0][0], "build")
        self.assertGreater(ranked[0][2], 15)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not on PATH")
class FfmpegTests(_Folder):
    def test_state_timeline_finds_the_colour_changes(self) -> None:
        clip = self.tmp / "states.mp4"
        parts = [("1f1f1f", 2), ("00a44e", 3), ("a37e0a", 2), ("c40c0c", 2)]
        inputs = []
        for c, d in parts:
            inputs += ["-f", "lavfi", "-i", f"color=c=0x{c}:s=160x90:r=30:d={d}"]
        concat = "".join(f"[{i}:v]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0,format=yuv420p"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", concat, str(clip)],
                       check=True, creationflags=NO_WINDOW)
        got = checks.state_timeline(clip, box={"x": 0, "y": 0, "w": 160, "h": 90},
                                    palette=["#1f1f1f", "#00a44e", "#a37e0a", "#c40c0c"], start=0, end=9, step_s=0.5)
        self.assertEqual([s for _, s in got], [0, 1, 2, 3])
        for (t, _), want in zip(got, [0, 2, 5, 7]):
            self.assertLessEqual(abs(t - want), 0.5)

    def test_verify_output_is_unknown_when_ffprobe_cannot_read_the_file(self) -> None:
        junk = self.tmp / "junk.mp4"
        junk.write_bytes(b"not a video")
        r = media.verify_output(junk, width=1920, height=1080, fps=30, duration=10)
        self.assertEqual(r.state, "unknown")
        self.assertFalse(r.ok)


if __name__ == "__main__":
    unittest.main()
