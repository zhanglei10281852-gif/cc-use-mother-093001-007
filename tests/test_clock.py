import unittest
from datetime import datetime, time

from helpers import HeatingWindow, FixedClock


class HeatingWindowTests(unittest.TestCase):
    def setUp(self):
        self.window = HeatingWindow(time(20, 0), time(8, 0))

    def test_crosses_midnight(self):
        self.assertTrue(self.window.crosses_midnight)
        self.assertFalse(HeatingWindow(time(8, 0), time(20, 0)).crosses_midnight)

    def test_contains_across_midnight(self):
        self.assertTrue(self.window.contains(datetime(2026, 1, 6, 23, 30)))
        self.assertTrue(self.window.contains(datetime(2026, 1, 7, 3, 0)))
        self.assertFalse(self.window.contains(datetime(2026, 1, 6, 12, 0)))
        # 半开区间：起点含、终点不含
        self.assertTrue(self.window.contains(datetime(2026, 1, 6, 20, 0)))
        self.assertFalse(self.window.contains(datetime(2026, 1, 7, 8, 0)))

    def test_intersect_merges_continuous_cross_midnight_window(self):
        segments = self.window.intersect(
            datetime(2026, 1, 6, 19, 0), datetime(2026, 1, 7, 9, 0)
        )
        self.assertEqual(
            segments,
            [(datetime(2026, 1, 6, 20, 0), datetime(2026, 1, 7, 8, 0))],
        )

    def test_intersect_clips_to_range(self):
        segments = self.window.intersect(
            datetime(2026, 1, 6, 22, 0), datetime(2026, 1, 7, 2, 0)
        )
        self.assertEqual(
            segments,
            [(datetime(2026, 1, 6, 22, 0), datetime(2026, 1, 7, 2, 0))],
        )

    def test_intersect_two_nights_gives_two_segments(self):
        segments = self.window.intersect(
            datetime(2026, 1, 6, 12, 0), datetime(2026, 1, 8, 12, 0)
        )
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0][0], datetime(2026, 1, 6, 20, 0))
        self.assertEqual(segments[1][0], datetime(2026, 1, 7, 20, 0))

    def test_daytime_window(self):
        w = HeatingWindow(time(6, 0), time(22, 0))
        self.assertTrue(w.contains(datetime(2026, 1, 6, 12, 0)))
        self.assertFalse(w.contains(datetime(2026, 1, 6, 23, 0)))


class FixedClockTests(unittest.TestCase):
    def test_advance(self):
        clock = FixedClock(datetime(2026, 1, 6, 23, 0))
        clock.advance(hours=2)
        self.assertEqual(clock.now(), datetime(2026, 1, 7, 1, 0))


if __name__ == "__main__":
    unittest.main()
