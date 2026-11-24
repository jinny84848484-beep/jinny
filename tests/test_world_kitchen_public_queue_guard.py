from pathlib import Path
import tempfile
import unittest

from world_kitchen.public_queue_guard import check_public_queue, MAX_QUEUE_BYTES

ROOT = Path(__file__).resolve().parents[1]


class PublicQueueGuardTests(unittest.TestCase):
    def check_fixture(self, raw):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"queue.json"
            p.write_bytes(raw)
            return check_public_queue(p)

    def test_empty_public_queue_is_no_action(self):
        self.assertEqual(self.check_fixture(b"[]\n"), (0, "no_ready_episode"))

    def test_any_queue_entry_rejected_without_manifest_read_or_output(self):
        secret_fixture = b'[{"date":"2099-12-31","manifest":"private-example.json"}]'
        self.assertEqual(self.check_fixture(secret_fixture), (2,"public_queue_must_be_empty"))
        self.assertEqual(self.check_fixture(b"[null]"), (2,"public_queue_must_be_empty"))

    def test_invalid_root_or_excess_size_rejected_opaquely(self):
        for raw in (b"\xff", b"private-name [", b"{}", b"["*1050, b" "*MAX_QUEUE_BYTES+b"[]"):
            with self.subTest(kind=raw[:8]):
                self.assertEqual(self.check_fixture(raw),(2,"public_queue_invalid"))

    def test_public_workflows_do_not_render_export_or_cache_real_queue(self):
        for name in ("world-kitchen.yml","world-kitchen-release-dry-run.yml"):
            yml=(ROOT/".github"/"workflows"/name).read_text(encoding="utf-8")
            self.assertIn("python -m world_kitchen.public_queue_guard",yml)
            for bad in ("uses: actions/upload-artifact","uses: actions/cache","world_kitchen.runner",
                        "world_kitchen.release_runner","runs/world_kitchen/","secrets."):
                self.assertNotIn(bad,yml)


if __name__ == "__main__":
    unittest.main()
