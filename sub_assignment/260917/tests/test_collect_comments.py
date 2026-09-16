import json
import tempfile
import unittest
from pathlib import Path

import collect_comments


class ParseVideoIdTests(unittest.TestCase):
    def test_supported_urls(self):
        expected = "S1bcy4h1mS8"
        values = [
            expected,
            f"https://youtu.be/{expected}?si=test",
            f"https://www.youtube.com/watch?v={expected}&list=test",
            f"https://www.youtube.com/shorts/{expected}",
            f"https://www.youtube.com/embed/{expected}",
        ]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(collect_comments.parse_video_id(value), expected)

    def test_playlist_url_without_video_is_rejected(self):
        with self.assertRaises(ValueError):
            collect_comments.parse_video_id("https://youtube.com/playlist?list=abc")


class DemoCollectionTests(unittest.TestCase):
    def setUp(self):
        self.demo = json.loads(
            collect_comments.DEMO_FILE.read_text(encoding="utf-8")
        )

    def test_two_demo_pages_make_four_rows(self):
        rows, stats = collect_comments.collect_demo_comments(
            self.demo,
            collected_at="2026-09-16T00:00:00Z",
            max_thread_pages=2,
        )
        self.assertEqual(len(rows), 4)
        self.assertEqual(stats["thread_pages_received"], 2)
        self.assertEqual(stats["top_level_comments_saved"], 2)
        self.assertEqual(stats["replies_saved"], 2)
        self.assertEqual(stats["stop_reason"], "no_next_page")

    def test_duplicate_comment_id_is_removed(self):
        duplicate = self.demo["reply_pages"]["DEMO_TOP_1"][0]["items"][0]
        self.demo["reply_pages"]["DEMO_TOP_1"][0]["items"].append(duplicate)
        rows, stats = collect_comments.collect_demo_comments(
            self.demo, collected_at="2026-09-16T00:00:00Z"
        )
        self.assertEqual(len(rows), 4)
        self.assertEqual(stats["duplicates_skipped_in_run"], 1)

    def test_save_results_writes_csv_and_log(self):
        rows, stats = collect_comments.collect_demo_comments(
            self.demo, collected_at="2026-09-16T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = collect_comments.save_results(
                rows,
                self.demo["video"],
                stats,
                max_thread_pages=0,
                max_reply_pages=0,
                mode="DEMO_FICTIONAL",
                collected_at="2026-09-16T00:00:00Z",
                output_root=Path(temp_dir),
            )
            self.assertTrue((folder / "comments.csv").is_file())
            self.assertTrue((folder / "collection_log.json").is_file())


class QuotaTrackerTests(unittest.TestCase):
    class FakeRequest:
        def execute(self):
            return {"ok": True}

    def test_budget_stops_before_extra_request(self):
        tracker = collect_comments.QuotaTracker(limit=1)
        self.assertEqual(tracker.execute(self.FakeRequest()), {"ok": True})
        with self.assertRaises(collect_comments.QuotaBudgetExceeded):
            tracker.execute(self.FakeRequest())
        self.assertEqual(tracker.used, 1)


if __name__ == "__main__":
    unittest.main()
