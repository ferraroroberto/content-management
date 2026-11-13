"""The recorder's /static mount: explicit cache headers and a build hash on every asset URL."""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from starlette.testclient import TestClient  # noqa: E402

from src.static_versioning import fleet_hash, stamp_assets  # noqa: E402
from tools.recorder import server  # noqa: E402
from tools.recorder.store import ChunkStore  # noqa: E402

IMMUTABLE = "public, max-age=31536000, immutable"


class StaticVersioningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.static = self.root / "static"
        shutil.copytree(server.STATIC, self.static)
        (self.root / "episode").mkdir()
        self.links = server.ensure_links(self.root / "episode")
        self.token = next(tok for tok, side in self.links.items() if side == "guest")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _client(self) -> TestClient:
        # The page and the mount read STATIC and ASSET_HASH, so patching both and building
        # the app serves an edited copy of the folder the way a restart after a deploy would.
        for name, value in (("STATIC", self.static), ("ASSET_HASH", fleet_hash(self.static))):
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        app = server.create_app(self.root / "episode", ChunkStore(self.root / "staging"), self.links)
        return TestClient(app)

    def _page_stamps(self, client: TestClient) -> set[str]:
        return set(re.findall(r"\?v=([0-9a-f]+)", client.get(f"/r/{self.token}").text))

    def test_the_page_revalidates_and_stamps_its_three_asset_urls(self) -> None:
        page = self._client().get(f"/r/{self.token}")
        self.assertEqual(page.headers["cache-control"], "no-store")
        stamp = fleet_hash(self.static)
        self.assertTrue(stamp)
        for url in ("/static/_vendored/rec-wave/rec-wave.css", "/static/_vendored/page-foot/page-foot.css",
                    "/static/recorder.js", "/static/_vendored/usage-log/usage-log.js"):
            self.assertIn(f"{url}?v={stamp}", page.text)

    def test_a_stamped_script_is_served_immutable_and_an_edit_changes_the_stamp(self) -> None:
        client = self._client()
        before = self._page_stamps(client)
        self.assertEqual(len(before), 1)
        (stamp,) = before
        script = client.get(f"/static/recorder.js?v={stamp}")
        self.assertEqual(script.headers["cache-control"], IMMUTABLE)
        self.assertEqual(script.status_code, 200)

        with (self.static / "recorder.js").open("a", encoding="utf-8") as handle:
            handle.write("\n// edited\n")
        after = self._page_stamps(self._client())
        self.assertEqual(len(after), 1)
        self.assertNotEqual(after, before)

    def test_the_scripts_own_dynamic_imports_carry_the_stamp(self) -> None:
        client = self._client()
        body = client.get("/static/recorder.js").text
        stamp = fleet_hash(self.static)
        self.assertIn(f'import("/static/_vendored/rec-wave/rec-wave.js?v={stamp}")', body)
        self.assertIn(f'import("/static/_vendored/page-foot/page-foot.js?v={stamp}")', body)

    def test_a_stylesheet_is_immutable_too(self) -> None:
        response = self._client().get("/static/_vendored/rec-wave/rec-wave.css")
        self.assertEqual(response.headers["cache-control"], IMMUTABLE)

    def test_stamping_twice_gives_the_same_body(self) -> None:
        once = stamp_assets('<script src="/static/recorder.js"></script>', "abc12345")
        self.assertEqual(once, '<script src="/static/recorder.js?v=abc12345"></script>')
        self.assertEqual(stamp_assets(once, "abc12345"), once)
        self.assertEqual(stamp_assets(once, "ffffffff"), once.replace("abc12345", "ffffffff"))

    def test_no_assets_means_no_stamp_and_no_failure(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(fleet_hash(empty), "")
        self.assertEqual(fleet_hash(self.root / "missing"), "")
        self.assertEqual(stamp_assets('"/static/a.js"', ""), '"/static/a.js"')


if __name__ == "__main__":
    unittest.main()
