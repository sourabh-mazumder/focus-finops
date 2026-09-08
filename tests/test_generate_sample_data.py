import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from focus_finops.focus_columns import ALL_FOCUS_IDS, NOT_NULL_FOCUS_IDS
from focus_finops.generate_sample_data import generate_rows


class TestGenerateSampleData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = generate_rows(months_back=2)

    def test_generates_rows(self):
        self.assertGreater(len(self.rows), 100)

    def test_required_columns_never_null(self):
        for row in self.rows:
            for col in NOT_NULL_FOCUS_IDS:
                self.assertIsNotNone(
                    row.get(col), f"{col} was None in a generated row: {row}"
                )

    def test_only_known_columns(self):
        known = set(ALL_FOCUS_IDS)
        for row in self.rows:
            self.assertTrue(set(row.keys()) <= known)

    def test_cost_ordering_list_gte_effective(self):
        # List price is never lower than the effective (discounted) cost.
        for row in self.rows:
            if row["ChargeCategory"] == "Usage":
                self.assertGreaterEqual(row["ListCost"] + 1e-6, row["EffectiveCost"])

    def test_billed_equals_effective_by_design(self):
        for row in self.rows:
            self.assertAlmostEqual(row["BilledCost"], row["EffectiveCost"], places=6)


if __name__ == "__main__":
    unittest.main()
