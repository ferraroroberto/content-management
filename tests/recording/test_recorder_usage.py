"""The recorder's usage log: events from every caller, reads for the owner only,
and no token route changed by the mount.

The usage database is always a per-test temp file, never the real one under the
runtime-data root.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.recording.test_recorder_usage -v
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from starlette.testclient import TestClient  # noqa: E402

from recorder import server  # noqa: E402
from recorder.store import ChunkStore  # noqa: E402

LOOPBACK = ("127.0.0.1", 50000)
GUEST_PEER = ("203.0.113.9", 50000)
FUNNEL = {"Tailscale-Funnel-Request": "?1", "X-Forwarded-For": "203.0.113.9"}
TAILNET = {"X-Forwarded-For": "100.101.102.103"}
BATCH = {
    "session_id": "11111111-2222-3333-4444-555555555555",
    "device_class": "desktop",
    "os_class": "windows",
    "events": [{"kind": "session_start"}, {"kind": "view", "id": "recorder"}, {"kind": "tap", "id": "start", "view": "recorder"}],
}


class UsageRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "runtime" / "usage.sqlite3"
        self.db.parent.mkdir()
        episode = root / "Some Episode"
        episode.mkdir()
        self.links = server.ensure_links(episode)
        self.token = {side: tok for tok, side in self.links.items()}
        app = server.create_app(episode, ChunkStore(root / "staging"), self.links, usage_db=self.db)
        self.owner = TestClient(app, client=LOOPBACK)
        self.guest = TestClient(app, client=LOOPBACK)
        self.stranger = TestClient(app, client=GUEST_PEER)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _rows(self) -> list[tuple]:
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute("SELECT kind, view_id, control_id FROM events ORDER BY id").fetchall()

    def test_a_guest_through_funnel_can_post_events(self) -> None:
        reply = self.guest.post("/api/usage/events", json=BATCH, headers=FUNNEL)
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json(), {"accepted": 3, "rejected": 0})
        self.assertEqual(self._rows(), [("session_start", None, None), ("view", "recorder", None), ("tap", "recorder", "start")])

    def test_events_are_open_to_any_peer(self) -> None:
        self.assertEqual(self.stranger.post("/api/usage/events", json=BATCH).status_code, 200)

    def test_the_summary_and_time_answer_the_owner_on_loopback(self) -> None:
        self.owner.post("/api/usage/events", json=BATCH)
        summary = self.owner.get("/api/usage/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertTrue(summary.json()["log_exists"])
        self.assertEqual(self.owner.get("/api/usage/time").status_code, 200)

    def test_the_summary_and_time_answer_a_tailnet_caller(self) -> None:
        for path in ("/api/usage/summary", "/api/usage/time"):
            with self.subTest(path=path):
                self.assertEqual(self.guest.get(path, headers=TAILNET).status_code, 200)

    def test_the_summary_and_time_refuse_a_funnelled_guest_and_an_off_machine_peer(self) -> None:
        self.owner.post("/api/usage/events", json=BATCH)
        for path in ("/api/usage/summary", "/api/usage/time"):
            for client, headers in ((self.guest, FUNNEL), (self.stranger, {})):
                with self.subTest(path=path, headers=bool(headers)):
                    reply = client.get(path, headers=headers)
                    self.assertEqual(reply.status_code, 403)
                    self.assertEqual(reply.json(), {"detail": "owner only"})

    def test_a_refused_read_leaks_nothing_of_the_log(self) -> None:
        self.owner.post("/api/usage/events", json=BATCH)
        body = self.guest.get("/api/usage/summary", headers=FUNNEL).text
        self.assertNotIn("recorder", body)
        self.assertNotIn("start", body)

    def test_without_a_usage_db_the_usage_paths_are_not_served(self) -> None:
        root = Path(self.tmp.name)
        bare = TestClient(server.create_app(root / "Some Episode", ChunkStore(root / "s"), self.links), client=LOOPBACK)
        self.assertEqual(bare.post("/api/usage/events", json=BATCH).status_code, 404)
        self.assertFalse(self.db.with_name("none.sqlite3").exists())


class TokenRoutesUnchangedTests(unittest.TestCase):
    """Every route the guest links use answers the same with the usage log mounted."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        episode = root / "Some Episode"
        episode.mkdir()
        self.links = server.ensure_links(episode)
        self.token = {side: tok for tok, side in self.links.items()}
        db = root / "usage.sqlite3"
        self.plain = TestClient(server.create_app(episode, ChunkStore(root / "a"), self.links), client=GUEST_PEER)
        self.mounted = TestClient(server.create_app(episode, ChunkStore(root / "b"), self.links, usage_db=db), client=GUEST_PEER)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _requests(self) -> list[tuple[str, str, dict]]:
        guest, rid = self.token["guest"], "0123456789abcdef"
        base = f"/api/{guest}"
        return [
            ("GET", f"/r/{guest}", {}),
            ("GET", "/r/nope", {}),
            ("GET", base, {}),
            ("GET", f"{base}/version", {}),
            ("GET", f"{base}/ice", {}),
            ("GET", "/api/nope", {}),
            ("GET", "/api/nope/ice", {}),
            ("GET", "/api/usage", {}),
            ("PUT", f"{base}/rec/{rid}/chunk/0", {"content": b"A"}),
            ("PUT", f"{base}/rec/{rid}/chunk/0", {"content": b"A"}),
            ("GET", f"{base}/rec/{rid}/have", {}),
            ("POST", f"{base}/rec/{rid}/finish", {"json": {"total": "x"}}),
            ("GET", "/static/recorder.js", {}),
        ]

    def test_each_token_route_answers_the_same_with_and_without_the_usage_log(self) -> None:
        for method, path, kwargs in self._requests():
            with self.subTest(method=method, path=path):
                plain = self.plain.request(method, path, **kwargs)
                mounted = self.mounted.request(method, path, **kwargs)
                self.assertEqual(mounted.status_code, plain.status_code)
                if "application/json" in plain.headers.get("content-type", ""):
                    self.assertEqual(mounted.json(), plain.json())
                else:
                    self.assertEqual(mounted.content, plain.content)

    def test_the_route_table_only_gains_the_usage_route_in_front(self) -> None:
        paths = lambda client: [route.path for route in client.app.routes]  # noqa: E731
        self.assertEqual(paths(self.mounted)[0], "/api/usage/{rest:path}")
        self.assertEqual(paths(self.mounted)[1:], paths(self.plain))

    def test_the_page_script_and_the_vendored_client_are_served(self) -> None:
        self.assertEqual(self.mounted.get("/static/_vendored/usage-log/usage-log.js").status_code, 200)
        page = self.mounted.get(f"/r/{self.token['guest']}").text
        self.assertIn("initUsageLog", page)


if __name__ == "__main__":
    unittest.main()
