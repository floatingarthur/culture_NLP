import json
import tempfile
import unittest
from pathlib import Path

import collect_comments
import upload_supabase


class ReadBatchTests(unittest.TestCase):
    def test_demo_csv_is_valid_upload_batch(self):
        demo = json.loads(collect_comments.DEMO_FILE.read_text(encoding="utf-8"))
        rows, stats = collect_comments.collect_demo_comments(
            demo, collected_at="2026-09-16T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = collect_comments.save_results(
                rows,
                demo["video"],
                stats,
                max_thread_pages=0,
                max_reply_pages=0,
                mode="DEMO_FICTIONAL",
                collected_at="2026-09-16T00:00:00Z",
                output_root=Path(temp_dir),
            )
            metadata, loaded_rows = upload_supabase.read_batch(folder)

        self.assertEqual(metadata["video"]["video_id"], "DEMO_VIDEO_1")
        self.assertEqual(len(loaded_rows), 4)
        self.assertEqual(
            {row["comment_type"] for row in loaded_rows}, {"top_level", "reply"}
        )
        self.assertTrue(
            all(row["parent_id"] for row in loaded_rows if row["comment_type"] == "reply")
        )


class ConnectionUriTests(unittest.TestCase):
    def test_password_is_url_encoded(self):
        result = upload_supabase._build_connection_uri(
            "postgresql://postgres.ref:[YOUR-PASSWORD]@db.example.com:5432/postgres",
            "p@ss/word",
        )
        self.assertIn("p%40ss%2Fword", result)
        self.assertNotIn("p@ss/word", result)


if __name__ == "__main__":
    unittest.main()
