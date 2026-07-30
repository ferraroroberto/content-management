"""Regression test: mapping.json's per-field ``optional_if_first_record`` flag
(issue #198) replaces a hardcoded Substack platform-name check inside
``process_array_data``. Confirms only the first record's declared field is
silently skipped, and any later record missing the same field still warns —
and that the behavior no longer depends on the platform name at all.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import logging
import unittest
from unittest.mock import MagicMock

from reporting.process import data_processor as dp


class OptionalIfFirstRecordTests(unittest.TestCase):
    def setUp(self):
        dp.logger = MagicMock()
        dp.logger.level = logging.INFO
        self.mapping_config = {
            "type": "array",
            "array_path": "data.items",
            "fields": {
                "post_id": {"path": "id", "type": "string", "required": True},
                "num_likes": {
                    "path": "likes",
                    "type": "integer",
                    "required": True,
                    "optional_if_first_record": True,
                },
            },
        }

    def test_first_record_missing_flagged_field_is_silently_skipped(self):
        data = {
            "platform": "substack",
            "data_type": "posts",
            "data": {"items": [
                {"id": "p1"},  # missing 'likes' -- expected on the first record
                {"id": "p2", "likes": 5},
            ]},
        }
        results = dp.process_array_data(data, self.mapping_config)
        self.assertEqual([r["post_id"] for r in results], ["p2"])
        dp.logger.warning.assert_not_called()

    def test_later_record_missing_flagged_field_still_warns(self):
        data = {
            "platform": "substack",
            "data_type": "posts",
            "data": {"items": [
                {"id": "p1", "likes": 3},
                {"id": "p2"},  # missing 'likes' on a non-first record
            ]},
        }
        results = dp.process_array_data(data, self.mapping_config)
        self.assertEqual([r["post_id"] for r in results], ["p1"])
        dp.logger.warning.assert_called()

    def test_flag_is_platform_agnostic(self):
        """The engine no longer special-cases the 'substack' platform name --
        any platform whose mapping declares the flag gets the same treatment."""
        data = {
            "platform": "some_other_platform",
            "data_type": "posts",
            "data": {"items": [{"id": "p1"}]},
        }
        results = dp.process_array_data(data, self.mapping_config)
        self.assertEqual(results, [])
        dp.logger.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
