import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from heating_diagnosis.contracts import HeatNode, RuntimeSample


class HeatingContractTests(unittest.TestCase):
    def test_node_keeps_parent(self):
        self.assertEqual(HeatNode("N2", "building", "N1").parent_id, "N1")

    def test_temperature_order_is_checked(self):
        with self.assertRaises(ValueError):
            RuntimeSample("N", 40, 50, 1)


if __name__ == "__main__": unittest.main()
