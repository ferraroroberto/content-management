"""Primary keys for the two tables the pipeline uploads (issue #373)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from reporting.process import supabase_uploader as su


class PrimaryKeyTests(unittest.TestCase):
    def test_posts_and_profile_keys(self) -> None:
        self.assertEqual(su.get_primary_keys("posts"), ["date", "platform", "data_type", "post_id"])
        self.assertEqual(su.get_primary_keys("Profile"), ["date", "platform", "data_type"])

    def test_returned_list_is_a_copy(self) -> None:
        su.get_primary_keys("posts").append("junk")
        self.assertNotIn("junk", su.get_primary_keys("posts"))

    def test_any_other_data_type_raises(self) -> None:
        for data_type in ("comments", "insights", "audience", "ads"):
            with self.subTest(data_type=data_type), self.assertRaises(ValueError):
                su.get_primary_keys(data_type)

    def test_unknown_data_type_fails_that_table_but_not_the_rest(self) -> None:
        frames = {
            "x_comments": pd.DataFrame({"a": [1]}),
            "x_profile": pd.DataFrame({"date": ["2026-10-07"]}),
        }
        with patch.object(su, "get_db_connection", return_value=MagicMock()), \
                patch.object(su, "upload_dataframe_to_db", return_value=True) as upload:
            ok = su.upload_all_dataframes(frames)
        self.assertFalse(ok)
        self.assertEqual([c.args[1] for c in upload.call_args_list], ["x_profile"])


if __name__ == "__main__":
    unittest.main()
