"""Tests for the recorder's chunk store and server (issue #341) — no browser."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from starlette.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from recorder import server, store  # noqa: E402
from recorder.store import ChunkStore, MissingChunks  # noqa: E402

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _rid() -> str:
    return str(uuid.uuid4())


def _fake_remux(src: Path, dst: Path) -> None:
    shutil.copyfile(src, dst)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ChunkStore(self.root / "staging")
        self.rid = _rid()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_re_sent_chunk_is_stored_once(self) -> None:
        self.assertTrue(self.store.put("guest", self.rid, 0, b"abc"))
        self.assertFalse(self.store.put("guest", self.rid, 0, b"abc"))
        self.assertEqual(self.store.have("guest", self.rid), [0])

    def test_have_reports_the_numbers_held_so_the_page_can_resume(self) -> None:
        for seq in (3, 0, 1):
            self.store.put("host", self.rid, seq, b"x")
        self.assertEqual(self.store.have("host", self.rid), [0, 1, 3])
        with self.assertRaises(MissingChunks) as ctx:
            self.store.finish("host", self.rid, 4, "mp4", self.root / "out", label="host")
        self.assertEqual(ctx.exception.missing, [2])

    def test_finish_joins_in_order_and_is_idempotent(self) -> None:
        for seq, data in ((2, b"C"), (0, b"A"), (1, b"B")):
            self.store.put("guest", self.rid, seq, data)
        with mock.patch.object(store, "remux", side_effect=_fake_remux), \
                mock.patch.object(store, "probe_duration", return_value=1.5):
            first = self.store.finish("guest", self.rid, 3, "mp4", self.root / "out", label="guest")
            again = self.store.finish("guest", self.rid, 3, "mp4", self.root / "out", label="guest")
        self.assertEqual(first, again)
        self.assertEqual((self.root / "out" / first["file"]).read_bytes(), b"ABC")
        self.assertTrue(first["file"].startswith("recorder - guest - "))
        self.assertEqual(self.store.have("guest", self.rid), [])  # staging cleared
        self.assertEqual(self.store.finished("guest", self.rid), first)

    def test_ids_numbers_and_extensions_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            self.store.put("guest", "../../etc", 0, b"x")
        with self.assertRaises(ValueError):
            self.store.put("guest", self.rid, -1, b"x")
        self.store.put("guest", self.rid, 0, b"x")
        with self.assertRaises(ValueError):
            self.store.finish("guest", self.rid, 1, "exe", self.root / "out", label="guest")

    def _rejoin(self, ext: str, fmt: list[str], audio: str) -> dict:
        # MediaRecorder hands out arbitrary byte slices of one streamed file.
        src = self.root / f"src.{ext}"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=320x180:rate=30:duration=3", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=3", "-c:v", "libx264", "-c:a", audio, "-shortest",
                        *fmt, str(src)], check=True, creationflags=store.NO_WINDOW)
        data = src.read_bytes()
        cuts = [0, 1000, 7777, len(data) // 2, len(data)]
        rid = _rid()
        for seq, (a, b) in enumerate(zip(cuts, cuts[1:])):
            self.store.put("host", rid, seq, data[a:b])
        return self.store.finish("host", rid, len(cuts) - 1, ext, self.root / ext, label="host")

    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_byte_chunks_of_a_fragmented_mp4_rejoin_to_a_file_with_its_duration(self) -> None:
        result = self._rejoin("mp4", ["-movflags", "frag_keyframe+empty_moov"], "aac")
        self.assertAlmostEqual(result["duration_s"], 3.0, delta=0.1)
        self.assertTrue(result["file"].endswith(".mp4"))

    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_h264_opus_webm_chunks_land_as_an_mp4(self) -> None:
        # Chrome's "video/webm;codecs=h264,opus" is Matroska with H.264 + Opus, streamed.
        result = self._rejoin("webm", ["-f", "matroska", "-live", "1"], "libopus")
        self.assertAlmostEqual(result["duration_s"], 3.0, delta=0.1)
        self.assertTrue(result["file"].endswith(".mp4"))


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.episode = Path(self.tmp.name) / "Some Episode"
        self.episode.mkdir()
        self.links = server.ensure_links(self.episode)
        self.token = {side: tok for tok, side in self.links.items()}
        app = server.create_app(self.episode, ChunkStore(Path(self.tmp.name) / "staging"), self.links)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_links_are_one_per_side_and_survive_a_restart(self) -> None:
        self.assertEqual(sorted(self.links.values()), ["guest", "host"])
        self.assertEqual(server.ensure_links(self.episode), self.links)
        saved = json.loads((self.episode / server.LINKS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(saved["links"], self.links)

    def test_a_request_without_a_valid_token_is_refused(self) -> None:
        rid = _rid()
        for method, path in (("GET", "/r/nope"), ("GET", "/api/nope"), ("PUT", f"/api/nope/rec/{rid}/chunk/0"),
                             ("GET", f"/api/nope/rec/{rid}/have"), ("POST", f"/api/nope/rec/{rid}/finish")):
            self.assertEqual(self.client.request(method, path, content=b"x").status_code, 404, path)

    def test_page_and_side(self) -> None:
        page = self.client.get(f"/r/{self.token['guest']}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("recorder.js", page.text)
        self.assertEqual(self.client.get(f"/api/{self.token['guest']}").json(), {"side": "guest"})
        self.assertEqual(self.client.get("/static/recorder.js").status_code, 200)

    def test_upload_resume_and_finish(self) -> None:
        api, rid = f"/api/{self.token['host']}/rec", _rid()
        self.assertEqual(self.client.put(f"{api}/{rid}/chunk/0", content=b"A").json(), {"ok": True, "duplicate": False})
        self.assertEqual(self.client.put(f"{api}/{rid}/chunk/0", content=b"A").json()["duplicate"], True)
        self.client.put(f"{api}/{rid}/chunk/2", content=b"C")
        self.assertEqual(self.client.get(f"{api}/{rid}/have").json(), {"have": [0, 2], "finished": None})
        missing = self.client.post(f"{api}/{rid}/finish", json={"total": 3, "ext": "mp4"})
        self.assertEqual((missing.status_code, missing.json()["missing"]), (409, [1]))
        self.client.put(f"{api}/{rid}/chunk/1", content=b"B")
        with mock.patch.object(store, "remux", side_effect=_fake_remux), \
                mock.patch.object(store, "probe_duration", return_value=2.0):
            done = self.client.post(f"{api}/{rid}/finish", json={"total": 3, "ext": "mp4"}).json()
        self.assertEqual((self.episode / server.OUTPUT_DIR / done["file"]).read_bytes(), b"ABC")
        self.assertEqual(self.client.get(f"{api}/{rid}/have").json()["finished"], done)

    def test_bad_input_is_a_400(self) -> None:
        api = f"/api/{self.token['host']}/rec"
        self.assertEqual(self.client.put(f"{api}/not-a-uuid/chunk/0", content=b"x").status_code, 400)
        self.assertEqual(self.client.post(f"{api}/{_rid()}/finish", json={}).status_code, 400)
        rid = _rid()
        self.client.put(f"{api}/{rid}/chunk/0", content=b"A")
        bad_kind = self.client.post(f"{api}/{rid}/finish", json={"total": 1, "ext": "webm", "kind": "x"})
        self.assertEqual(bad_kind.status_code, 400)

    def test_the_remote_voice_reference_gets_its_own_name(self) -> None:
        api, rid = f"/api/{self.token['guest']}/rec", _rid()
        self.client.put(f"{api}/{rid}/chunk/0", content=b"A")
        with mock.patch.object(store, "remux", side_effect=_fake_remux), \
                mock.patch.object(store, "probe_duration", return_value=1.0):
            done = self.client.post(f"{api}/{rid}/finish", json={"total": 1, "ext": "webm", "kind": "ref"}).json()
        self.assertTrue(done["file"].startswith("recorder - guest remote-ref - "))


class SignallingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        episode = Path(self.tmp.name) / "Some Episode"
        episode.mkdir()
        links = server.ensure_links(episode)
        self.token = {side: tok for tok, side in links.items()}
        ice = [{"urls": ["stun:stun.example:3478"]}]
        self.client = TestClient(server.create_app(episode, ChunkStore(Path(self.tmp.name) / "s"), links, ice))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ws(self, side: str, peer: str):
        return self.client.websocket_connect(f"/ws/{self.token[side]}?peer={peer}")

    def test_a_bad_link_or_peer_id_is_refused(self) -> None:
        with self.assertRaises(WebSocketDisconnect) as bad_link:
            with self.client.websocket_connect("/ws/nope?peer=aaaaaaaa"):
                pass
        self.assertEqual(bad_link.exception.code, server.UNKNOWN_LINK)
        with self.assertRaises(WebSocketDisconnect):
            with self._ws("host", "../x"):
                pass

    def test_presence_and_relay_between_the_two_sides(self) -> None:
        with self._ws("host", "aaaaaaaa") as host:
            self.assertEqual(host.receive_json(), {"type": "peer", "present": False, "peer": None})
            with self._ws("guest", "bbbbbbbb") as guest:
                self.assertEqual(host.receive_json(), {"type": "peer", "present": True, "peer": "bbbbbbbb"})
                self.assertEqual(guest.receive_json(), {"type": "peer", "present": True, "peer": "aaaaaaaa"})
                host.send_json({"type": "something else"})  # not a signal: dropped
                host.send_json({"type": "offer", "sdp": "v=0"})
                self.assertEqual(guest.receive_json(), {"type": "offer", "sdp": "v=0", "from": "host"})
                guest.send_json({"type": "candidate", "candidate": {"candidate": "c"}})
                self.assertEqual(host.receive_json()["from"], "guest")
            self.assertEqual(host.receive_json(), {"type": "peer", "present": False, "peer": None})

    def test_one_socket_per_side_and_a_reconnect_replaces_the_stale_one(self) -> None:
        with self._ws("guest", "bbbbbbbb") as guest:
            guest.receive_json()
            with self._ws("host", "aaaaaaaa") as first:
                first.receive_json()
                guest.receive_json()
                with self._ws("host", "cccccccc") as second:
                    with self.assertRaises(WebSocketDisconnect) as replaced:
                        while True:
                            first.receive_json()
                    self.assertEqual(replaced.exception.code, server.REPLACED)
                    second.receive_json()
                    self.assertEqual(guest.receive_json(), {"type": "peer", "present": True, "peer": "cccccccc"})
                    guest.send_json({"type": "answer", "sdp": "x"})
                    self.assertEqual(second.receive_json()["type"], "answer")

    def test_ice_config(self) -> None:
        self.assertEqual(self.client.get(f"/api/{self.token['host']}/ice").json(),
                         {"iceServers": [{"urls": ["stun:stun.example:3478"]}]})
        self.assertEqual(self.client.get("/api/nope/ice").status_code, 404)
        self.assertEqual(server.ice_servers({}), [{"urls": server.DEFAULT_STUN}])
        cfg = {"turn": {"urls": ["turn:relay.example:443?transport=tcp"], "username": "u", "credential": "c"}}
        with mock.patch.dict("os.environ", {"RECORDER_TURN_CREDENTIAL": "from-env"}):
            turn = server.ice_servers(cfg)[1]
        self.assertEqual(turn, {"urls": ["turn:relay.example:443?transport=tcp"], "username": "u",
                                "credential": "from-env"})


if __name__ == "__main__":
    unittest.main()
