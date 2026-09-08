import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from focus_finops.focus_columns import (
    ALL_FOCUS_IDS,
    FOCUS_COLUMNS,
    FOCUS_ID_TO_DB,
    NOT_NULL_FOCUS_IDS,
)


class TestFocusColumns(unittest.TestCase):
    def test_column_count_matches_focus_v1_4_spec(self):
        # FOCUS v1.4 "Cost and Usage" dataset has 65 columns.
        self.assertEqual(len(FOCUS_COLUMNS), 65)

    def test_no_duplicate_focus_ids(self):
        ids = [c[0] for c in FOCUS_COLUMNS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_duplicate_db_columns(self):
        db_cols = [c[1] for c in FOCUS_COLUMNS]
        self.assertEqual(len(db_cols), len(set(db_cols)))

    def test_valid_kinds(self):
        valid = {"text", "numeric", "timestamp", "json", "boolean"}
        for focus_id, db_col, kind in FOCUS_COLUMNS:
            self.assertIn(kind, valid, f"{focus_id} has unexpected kind {kind}")

    def test_not_null_ids_are_real_columns(self):
        for focus_id in NOT_NULL_FOCUS_IDS:
            self.assertIn(focus_id, FOCUS_ID_TO_DB)

    def test_all_focus_ids_matches_columns(self):
        self.assertEqual(ALL_FOCUS_IDS, [c[0] for c in FOCUS_COLUMNS])


if __name__ == "__main__":
    unittest.main()
