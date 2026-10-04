import sys
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from heating_diagnosis import DailyHeatingWindow, FixedClock


class CrossMidnightWindowTests(unittest.TestCase):
    def setUp(self):
        self.window = DailyHeatingWindow(time(22, 0), time(6, 0))

    def test_contains_across_midnight(self):
        self.assertTrue(self.window.contains(datetime(2026, 1, 5, 23, 30)))
        self.assertTrue(self.window.contains(datetime(2026, 1, 6, 3, 0)))
        self.assertFalse(self.window.contains(datetime(2026, 1, 6, 12, 0)))

    def test_current_interval_before_midnight(self):
        clock = FixedClock(datetime(2026, 1, 5, 23, 30))
        start, end = self.window.current_interval(clock)
        self.assertEqual(start, datetime(2026, 1, 5, 22, 0))
        self.assertEqual(end, datetime(2026, 1, 6, 6, 0))

    def test_current_interval_after_midnight(self):
        clock = FixedClock(datetime(2026, 1, 6, 2, 0))
        start, end = self.window.current_interval(clock)
        self.assertEqual(start, datetime(2026, 1, 5, 22, 0))
        self.assertEqual(end, datetime(2026, 1, 6, 6, 0))

    def test_outside_window_raises(self):
        clock = FixedClock(datetime(2026, 1, 6, 12, 0))
        with self.assertRaises(ValueError):
            self.window.current_interval(clock)

    def test_same_day_window(self):
        w = DailyHeatingWindow(time(8, 0), time(20, 0))
        clock = FixedClock(datetime(2026, 1, 6, 10, 0))
        self.assertEqual(w.current_interval(clock),
                         (datetime(2026, 1, 6, 8, 0), datetime(2026, 1, 6, 20, 0)))
        self.assertFalse(w.crosses_midnight())


if __name__ == "__main__":
    unittest.main()
