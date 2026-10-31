"""Whitelisting a commenter hands their pending comments to the owner without the canned AI reply.

The cascade flips pending rows to ``human``; a ``suggested_reply`` left behind would render on
the real-comments card as if the AI acknowledgement were meant for the owner's reader.

Run: & .\\.venv\\Scripts\\python.exe -m pytest tests/linkedin/test_linkedin_whitelist_cascade.py -q
"""

from __future__ import annotations

import unittest
from unittest import mock

from engagement.db import client


class CascadeWhitelistTests(unittest.TestCase):
    def test_pending_rows_lose_their_canned_reply(self) -> None:
        sb = mock.MagicMock()
        chain = sb.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value
        chain.execute.return_value.data = [{"comment_id": "c1"}]
        with mock.patch.object(client, "supabase_client", return_value=sb):
            flipped = client.cascade_whitelist_pending("linkedin", "https://linkedin.com/in/x")
        payload = sb.table.return_value.update.call_args.args[0]
        self.assertEqual(flipped, 1)
        self.assertEqual(payload["classification"], "human")
        self.assertIn("suggested_reply", payload)
        self.assertIsNone(payload["suggested_reply"])


if __name__ == "__main__":
    unittest.main()
