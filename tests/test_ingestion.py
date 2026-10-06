import unittest

from helpers import batch, complaint, make_service, outdoor, sample


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.service, self.clock = make_service()

    def test_same_batch_is_not_counted_twice(self):
        b = batch("B-1", "samples", [sample("HX-1", 21, 0, 50.0)])
        first = self.service.ingest(b)
        second = self.service.ingest(b)
        self.assertEqual(first.status, "accepted")
        self.assertEqual(first.accepted_count, 1)
        self.assertEqual(second.status, "duplicate")
        self.assertEqual(second.accepted_count, 0)
        self.assertEqual(len(self.service.store.samples), 1)

    def test_same_record_in_different_batch_is_skipped(self):
        self.service.ingest(batch("B-1", "samples", [sample("HX-1", 21, 0, 50.0)]))
        result = self.service.ingest(
            batch("B-2", "samples", [sample("HX-1", 21, 0, 50.0),
                                     sample("HX-1", 21, 30, 50.0)])
        )
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(len(self.service.store.samples), 2)

    def test_all_kinds_ingest(self):
        self.service.ingest(batch("O-1", "outdoor", [outdoor(21)]))
        self.service.ingest(batch("C-1", "complaints", [complaint("C-1", "B-1", 21, 5)]))
        self.service.ingest(batch("S-1", "samples", [sample("B-1", 21, 0, 43.0)]))
        self.assertEqual(len(self.service.store.outdoor), 1)
        self.assertEqual(len(self.service.store.complaints), 1)
        self.assertEqual(len(self.service.store.samples), 1)

    def test_complaint_dedup_by_id_across_batches(self):
        self.service.ingest(batch("C-1", "complaints", [complaint("C-1", "B-1", 21, 5)]))
        result = self.service.ingest(batch("C-2", "complaints", [complaint("C-1", "B-1", 21, 5)]))
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(len(self.service.store.complaints), 1)

    def test_invalid_kind_rejected(self):
        with self.assertRaises(ValueError):
            batch("X", "telemetry", [])


if __name__ == "__main__":
    unittest.main()
