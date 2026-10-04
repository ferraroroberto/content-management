"""Tests for the recorder sync stage (issue #343): synthetic speech, real Opus, no hub.

A session is built the way the recorder makes one: each side's own track on
its own clock, and each side's reference of the *other* voice, recorded on
its own clock, delayed by the call and degraded by a 24 kbps Opus round trip.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcast import sync  # noqa: E402
from podcast.episode import Episode, load_episode  # noqa: E402
from podcast.media import NO_WINDOW, read_wav, run_ffmpeg, write_wav  # noqa: E402

RATE = sync.RATE
FRAME = 1 / 24
FFMPEG = shutil.which("ffmpeg") is not None
MARGIN_S = 120.0


def synth_speech(seconds: float, seed: int) -> np.ndarray:
    """Speech-like audio: voiced syllables (harmonics of a wandering pitch plus
    breath noise) of 80-350 ms, short gaps and the odd longer pause."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * RATE), dtype=np.float32)
    t = 0.0
    while t < seconds - 1:
        n = int(rng.uniform(0.08, 0.35) * RATE)
        k = np.arange(n) / RATE
        f0 = rng.uniform(90, 230)
        voiced = sum(np.sin(2 * np.pi * f0 * h * k + rng.uniform(0, 6.28)) / h for h in range(1, 12))
        syllable = (voiced + 0.4 * rng.standard_normal(n)) * np.hanning(n) * rng.uniform(0.2, 1.0)
        a = int(t * RATE)
        out[a:a + n] += syllable[: len(out) - a].astype(np.float32)
        t += n / RATE + (rng.uniform(0.6, 1.6) if rng.random() < 0.12 else rng.uniform(0.03, 0.25))
    return 0.1 * out / (np.abs(out).max() + 1e-9)


def opus_round_trip(x: np.ndarray, scratch: Path, name: str) -> np.ndarray:
    """What the call does to a voice: 24 kbps Opus, then back to 8 kHz."""
    src = write_wav(scratch / f"{name}.wav", np.clip(x * 32767, -32768, 32767), RATE)
    run_ffmpeg(["-i", str(src), "-c:a", "libopus", "-b:a", "24k", str(scratch / f"{name}.ogg")])
    run_ffmpeg(["-i", str(scratch / f"{name}.ogg"), "-ac", "1", "-ar", str(RATE), str(scratch / f"{name}_back.wav")])
    samples, _ = read_wav(scratch / f"{name}_back.wav")
    return samples.astype(np.float32) / 32768


def sample(voice: np.ndarray, times: np.ndarray) -> np.ndarray:
    """``voice`` (which starts at -MARGIN_S on its own clock) read at ``times``."""
    idx = (times + MARGIN_S) * RATE
    return np.interp(idx, np.arange(len(voice)), voice, left=0, right=0).astype(np.float32)


def session(scratch: Path, *, d0: float, rho: float, l_to_host: float, l_to_guest: float,
            r_host: float, r_guest: float, seconds: float) -> tuple[dict, dict, dict]:
    """Host time h, guest time g: h = g + D(h), D(h) = d0 + rho*h."""
    host_voice = synth_speech(seconds + 2 * MARGIN_S, seed=1)
    guest_voice = synth_speech(seconds + 2 * MARGIN_S, seed=2)
    t = np.arange(int(seconds * RATE)) / RATE
    to_guest = lambda h: h * (1 - rho) - d0  # noqa: E731
    to_host = lambda g: (g + d0) / (1 - rho)  # noqa: E731
    own = {"host": sample(host_voice, t), "guest": sample(guest_voice, t)}
    heard_on_host = sample(guest_voice, to_guest(r_host + t - l_to_host))      # host's reference
    heard_on_guest = sample(host_voice, to_host(r_guest + t - l_to_guest))     # guest's reference
    refs = {"host": opus_round_trip(heard_on_host, scratch, "ref_host"),
            "guest": opus_round_trip(heard_on_guest, scratch, "ref_guest")}
    return refs, own, {"host": r_host, "guest": r_guest}


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class EstimateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.scratch = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_offset_is_recovered_within_a_frame_through_the_call_codec(self) -> None:
        # Uneven latency (120 vs 160 ms) is what the symmetric solve cannot cancel: 20 ms left.
        refs, own, r = session(self.scratch, d0=37.25, rho=0.0, l_to_host=0.12, l_to_guest=0.16,
                               r_host=1.8, r_guest=0.6, seconds=240)
        report = sync.estimate(refs, own, r)
        self.assertAlmostEqual(report["offset_s"], 37.25, delta=FRAME)
        self.assertEqual(report["drift_ppm"], 0.0)
        self.assertAlmostEqual(report["round_trip_s"], 0.28, delta=FRAME)
        self.assertEqual(report["method"], "both references")

    def test_drift_is_measured_and_the_end_lands_within_a_frame(self) -> None:
        # 500 ppm over 6 min drifts 180 ms, as 50 ppm does over an hour.
        refs, own, r = session(self.scratch, d0=-12.5, rho=500e-6, l_to_host=0.1, l_to_guest=0.1,
                               r_host=0.4, r_guest=2.2, seconds=360)
        report = sync.estimate(refs, own, r)
        rho = report["drift_ppm"] / 1e6
        for t in (0.0, 360.0):
            est = report["offset_s"] + rho * t
            self.assertAlmostEqual(est, -12.5 + 500e-6 * t, delta=FRAME, msg=f"at {t} s")

    def test_fifty_ppm_over_an_hour_lands_within_a_frame_at_both_ends(self) -> None:
        # The slow rate a real pair of clocks has: below a tenth of the case
        # above, so a drift floor set too high would hide it, and an hour long,
        # so the start and end windows come from capped EDGE_S spans. ~30 s.
        refs, own, r = session(self.scratch, d0=21.3, rho=50e-6, l_to_host=0.1, l_to_guest=0.1,
                               r_host=1.1, r_guest=0.7, seconds=3600)
        report = sync.estimate(refs, own, r)
        self.assertAlmostEqual(report["drift_ppm"], 50.0, delta=5.0)
        rho = report["drift_ppm"] / 1e6
        for t in (0.0, 3600.0):
            est = report["offset_s"] + rho * t
            self.assertAlmostEqual(est, 21.3 + 50e-6 * t, delta=FRAME, msg=f"at {t} s")

    def test_one_reference_is_enough_but_says_so(self) -> None:
        refs, own, r = session(self.scratch, d0=5.0, rho=0.0, l_to_host=0.1, l_to_guest=0.1,
                               r_host=0.5, r_guest=0.5, seconds=200)
        report = sync.estimate({"host": refs["host"]}, own, {"host": r["host"]})
        self.assertIn("only", report["method"])
        self.assertAlmostEqual(report["offset_s"], 5.0 + 0.1, delta=FRAME)  # off by the one-way delay

    def test_near_silence_is_refused_with_the_peak_ratios(self) -> None:
        rng = np.random.default_rng(3)
        hiss = lambda: (1e-4 * rng.standard_normal(200 * RATE)).astype(np.float32)  # noqa: E731
        with self.assertRaises(sync.SyncError) as ctx:
            sync.estimate({"host": hiss(), "guest": hiss()}, {"host": hiss(), "guest": hiss()},
                          {"host": 0.0, "guest": 0.0})
        # Hiss passes as "sound" but correlates with nothing: refused, with the ratios it got.
        self.assertRegex(str(ctx.exception), r"no window cleared a peak ratio of 8\.0 \(got \d+\.\d")


class PureTests(unittest.TestCase):
    def test_solve_cancels_the_latency(self) -> None:
        d, l1, l2, rh, rg = 10.0, 0.2, 0.2, 1.0, 3.0
        c1, c2 = d + l1 - rh, -d + l2 - rg
        self.assertAlmostEqual(sync.solve(c1, c2, rh, rg)[0], d)

    def test_small_drift_is_left_alone(self) -> None:
        self.assertEqual(sync.drift([(0.0, 1.0), (3600.0, 1.0 + 1e-6 * 3600)]), 0.0)
        self.assertAlmostEqual(sync.drift([(0.0, 1.0), (1000.0, 1.05)]), 50e-6)

    def test_retime_filters(self) -> None:
        video, audio = sync.retime_filters(1.5, 0.0, 30)
        self.assertEqual(video, "setpts=(PTS-STARTPTS+1.500000/TB)/1.000000000,trim=start=0,fps=30:start_time=0")
        self.assertEqual(audio, "adelay=1500.000:all=1,atempo=1.000000000,aresample=48000")
        self.assertTrue(sync.retime_filters(-2.0, 0.0, 30)[1].startswith("atrim=start=2.000000"))


def _episode(folder: Path) -> Episode:
    return Episode(folder=folder, guest="Guest Person", guest_display="Guest Person", guest_first="Guest",
                   guest_pronoun_possessive="their",
                   tracks={"host": folder / "video editing" / "recorder - host - 1.mp4",
                           "guest": folder / "video editing" / "recorder - guest - 1.mp4"})


class StageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        (self.folder / "video editing").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _sidecar(self, name: str, **meta) -> None:
        (self.folder / "video editing" / f"{name}.json").write_text(json.dumps(meta), encoding="utf-8")

    def test_references_pair_with_the_episode_tracks_by_recording_id(self) -> None:
        self._sidecar("recorder - host - 1", kind="main", side="host", rid="A")
        self._sidecar("recorder - guest - 1", kind="main", side="guest", rid="B")
        self._sidecar("recorder - host remote-ref - 1", kind="ref", side="host", main="A", offset_in_main_s=1.25)
        self._sidecar("recorder - guest remote-ref - 1", kind="ref", side="guest", main="B", offset_in_main_s=0.5)
        self._sidecar("recorder - guest remote-ref - 0", kind="ref", side="guest", main="OLD", offset_in_main_s=9)
        refs = sync.references(_episode(self.folder))
        self.assertEqual({s: (r.path.name, r.offset_in_main_s) for s, r in refs.items()},
                         {"host": ("recorder - host remote-ref - 1.mp4", 1.25),
                          "guest": ("recorder - guest remote-ref - 1.mp4", 0.5)})

    def test_a_session_without_references_is_left_as_is(self) -> None:
        ep = _episode(self.folder)
        self.assertIsNone(sync.run(ep, {"work_dir": str(self.folder / "work")}, None))
        self.assertFalse((ep.package / sync.SYNC_FILE).exists())

    def test_a_trial_folder_never_writes_beside_the_source_tracks(self) -> None:
        # A trial episode (issue #350) reads another folder's tracks by absolute path:
        # the re-timed guest track must land in the trial folder, not beside the source.
        source = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, source)
        (source / "video editing").mkdir()
        host, guest = source / "video editing" / "h.mp4", source / "video editing" / "g.mp4"
        (self.folder / "episode.json").write_text(json.dumps({
            "guest": "Guest Person", "tracks": {"host": host.as_posix(), "guest": guest.as_posix()}}),
            encoding="utf-8")
        cfg = {"package_dirname": "podcast package", "work_dir": str(self.folder / "work")}
        ep = load_episode(self.folder, cfg)
        written = []
        with mock.patch.object(sync, "references", return_value={"host": object()}), \
                mock.patch.object(sync, "measure", return_value={"offset_s": 1.0, "drift_ppm": 0.0,
                                                                 "method": "both references"}), \
                mock.patch.object(sync, "retime", side_effect=lambda src, dst, *a: written.append(dst)):
            sync.run(ep, cfg, None)
        self.assertEqual(written, [self.folder / "video editing" / sync.OUTPUT])
        self.assertEqual(sorted(p.name for p in (source / "video editing").iterdir()), [])
        reread = load_episode(self.folder, cfg)
        self.assertEqual(reread.tracks, {"host": host, "guest": self.folder / "video editing" / sync.OUTPUT})

    def test_the_episode_reads_the_synced_tracks(self) -> None:
        (self.folder / "episode.json").write_text(json.dumps({
            "guest": "Guest Person", "tracks": {"host": "video editing/h.mp4", "guest": "video editing/g.mp4"}}),
            encoding="utf-8")
        package = self.folder / "podcast package"
        package.mkdir()
        (package / sync.SYNC_FILE).write_text(json.dumps({"tracks": {"host": "video editing/h.mp4",
                                                                     "guest": "video editing/synced - guest.mp4"}}),
                                              encoding="utf-8")
        ep = load_episode(self.folder, {"package_dirname": "podcast package"})
        self.assertEqual(ep.tracks["guest"].name, "synced - guest.mp4")
        self.assertEqual(ep.tracks["host"].name, "h.mp4")

    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_retime_shifts_the_guest_track(self) -> None:
        src, dst = self.folder / "g.mp4", self.folder / "g_synced.mp4"
        run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=160x90:rate=30:duration=6", "-f", "lavfi", "-i",
                    "sine=frequency=1000:duration=6", "-af", "volume=enable='lt(t,1)':volume=0",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src)])
        sync.retime(src, dst, 1.5, 0.0, "libx264")
        run_ffmpeg(["-i", str(dst), "-ac", "1", "-ar", str(RATE), str(self.folder / "out.wav")])
        samples, _ = read_wav(self.folder / "out.wav")
        onset = np.argmax(np.abs(samples) > 1000) / RATE
        self.assertAlmostEqual(onset, 2.5, delta=FRAME)  # the tone started at 1 s, shifted by 1.5 s
        duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                         "default=nw=1:nk=1", str(dst)], capture_output=True, text=True,
                                        creationflags=NO_WINDOW).stdout)
        self.assertAlmostEqual(duration, 7.5, delta=0.2)


if __name__ == "__main__":
    unittest.main()
