"""The recorder's call end to end (issue #342): two pages, one per side, in
Playwright's Chromium with fake camera and mic, against a local server.

It checks what the room tests in ``test_recorder.py`` cannot: the call
connects, the call picture is capped near 720p while each side records its
full-resolution stream, and both sides upload a main recording plus a
reference of the other side's voice. Skipped when ffmpeg or Playwright's
Chromium is missing.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import uvicorn  # noqa: E402

from recorder import server  # noqa: E402
from recorder.store import NO_WINDOW, ChunkStore  # noqa: E402

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
CHROME_ARGS = [
    "--use-fake-device-for-media-stream",
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
    # Plain host candidates: headless Chromium cannot always resolve the
    # mDNS names it hides local addresses behind.
    "--disable-features=WebRtcHideLocalIpsWithMdns",
]
# An unreachable local STUN keeps the test off the network; host candidates connect.
ICE = [{"urls": ["stun:127.0.0.1:9"]}]
RECORD_S = 4
TIMEOUT_S = 60

# The call's video sender, from the page's own RTCPeerConnection: the cap it
# was given and the picture it is sending.
CALL_SENDER_JS = """async () => {
  const sender = pc.getSenders().find((s) => s.track && s.track.kind === "video");
  const [encoding] = sender.getParameters().encodings;
  let height = null;
  for (const r of (await sender.getStats()).values()) {
    if (r.type === "outbound-rtp" && r.frameHeight) height = r.frameHeight;
  }
  return { scale: encoding.scaleResolutionDownBy, maxBitrate: encoding.maxBitrate, height };
}"""


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "json", str(path)], check=True, capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    return json.loads(out)


class _Server:
    """The recorder app on an ephemeral loopback port, in a thread."""

    def __init__(self, app) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True)

    def __enter__(self) -> "_Server":
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("the test server did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(10)
        self.sock.close()


@unittest.skipUnless(FFMPEG, "ffmpeg/ffprobe not on PATH")
class CallInTheBrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError:
            self.skipTest("playwright not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.episode = Path(self.tmp.name) / "Some Episode"
        self.episode.mkdir()
        links = server.ensure_links(self.episode)
        self.token = {side: tok for tok, side in links.items()}
        self.pw = self.enterContext(sync_playwright())
        try:
            self.browser = self.pw.chromium.launch(args=CHROME_ARGS)
        except Exception as exc:  # noqa: BLE001 - no browser installed is a skip, not a failure
            self.skipTest(f"Playwright's Chromium is not available: {exc}")
        self.addCleanup(self.browser.close)
        # Started last so it stops first, closing the pages' sockets itself.
        app = server.create_app(self.episode, ChunkStore(Path(self.tmp.name) / "staging"), links, ICE)
        self.web = self.enterContext(_Server(app))

    def _open(self, side: str):
        page = self.browser.new_context().new_page()
        page.goto(f"http://127.0.0.1:{self.web.port}/r/{self.token[side]}")
        return page

    def _wait(self, pages: dict, what: str, check) -> dict:
        """Poll each page's ``window.__recorder`` until ``check`` holds on all of them."""
        deadline = time.monotonic() + TIMEOUT_S
        while True:
            states = {side: page.evaluate("window.__recorder || null") for side, page in pages.items()}
            if all(state and check(state) for state in states.values()):
                return states
            if time.monotonic() > deadline:
                self.fail(f"timed out waiting for {what}: {states}")
            time.sleep(0.5)

    def test_the_call_connects_and_both_sides_upload_a_recording_and_a_reference(self) -> None:
        pages = {side: self._open(side) for side in server.SIDES}
        self._wait(pages, "the call to connect", lambda s: s["call"] == "connected")
        cap_height, cap_bps = page_constant("CALL_HEIGHT"), page_constant("CALL_BPS")
        captured = {}
        for side, page in pages.items():
            page.wait_for_function("!document.getElementById('start').disabled", timeout=TIMEOUT_S * 1000)
            captured[side] = page.evaluate("document.getElementById('preview').videoHeight")
            self.assertGreater(captured[side], cap_height, f"{side} camera must capture above the call's cap")
            sender = page.evaluate(CALL_SENDER_JS)
            self.assertEqual(sender["maxBitrate"], cap_bps, side)
            self.assertAlmostEqual(captured[side] / sender["scale"], cap_height, delta=1, msg=side)
        for page in pages.values():
            page.click("#start")
        self._wait(pages, "both sides to record", lambda s: s["recording"])
        time.sleep(RECORD_S)
        for side, page in pages.items():
            # A few seconds in, the call has ramped up; it never sends above the cap.
            self.assertLessEqual(page.evaluate(CALL_SENDER_JS)["height"] or 0, cap_height, side)
            page.click("#stop")
        states = self._wait(pages, "both uploads to finish",
                            lambda s: not s["recording"] and s["results"] and s["refs"] and not s["pending"])
        for side, state in states.items():
            self.assertEqual(state["errors"], [], side)

        out = self.episode / server.OUTPUT_DIR
        for side in server.SIDES:
            mains = [json.loads(p.read_text(encoding="utf-8")) for p in out.glob(f"recorder - {side} - *.json")]
            refs = [json.loads(p.read_text(encoding="utf-8")) for p in out.glob(f"recorder - {side} remote-ref - *.json")]
            self.assertEqual(len(mains), 1, f"{side} main recording")
            self.assertGreaterEqual(len(refs), 1, f"{side} reference")
            main, ref = mains[0], refs[0]
            # The sync stage pairs a reference with its main recording by id (#343).
            self.assertEqual((ref["kind"], ref["main"]), ("ref", main["rid"]))
            self.assertGreaterEqual(ref["offset_in_main_s"], 0)
            video = [s for s in _probe(out / main["file"])["streams"] if s["codec_type"] == "video"]
            self.assertEqual(video[0]["height"], captured[side], f"{side} records at its full capture height")
            self.assertGreater(main["duration_s"], RECORD_S - 1, f"{side} main duration")
            ref_probe = _probe(out / ref["file"])
            self.assertEqual([s["codec_type"] for s in ref_probe["streams"]], ["audio"], f"{side} reference streams")
            self.assertGreater(float(ref_probe["format"]["duration"]), RECORD_S - 2, f"{side} reference duration")


def page_constant(name: str) -> int:
    """A numeric constant of recorder.js (``CALL_HEIGHT``, ``CALL_BPS``)."""
    js = (Path(server.STATIC) / "recorder.js").read_text(encoding="utf-8")
    return int(js.split(f"const {name} = ", 1)[1].split(";", 1)[0])


if __name__ == "__main__":
    unittest.main()
