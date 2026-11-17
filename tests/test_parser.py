import os
import tempfile
import time
import unittest

from spend_teardown.parser import SchemaError, scan
from tests import fixtures as fx


def epoch(minutes):
    return (fx.T0.timestamp()) + minutes * 60


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_streaming_lines_keep_last(self):
        fx.write(self.root, fx.session_file(), [
            fx.assistant(0, "msg_1", out=5),
            fx.assistant(0, "msg_1", out=50),
            fx.assistant(0, "msg_1", out=500),
        ])
        sc = scan(self.root)
        self.assertEqual(len(sc.calls), 1)
        self.assertEqual(sc.calls[0].out, 500)

    def test_global_dedup_across_resumed_session(self):
        original = [fx.assistant(0, "msg_1", cw1h=50_000), fx.assistant(5, "msg_2", cr=50_000)]
        fx.write(self.root, fx.session_file("aaaa"), original, mtime=time.time() - 100)
        # a resumed session copies the earlier records into a new file, then continues
        fx.write(self.root, fx.session_file("bbbb"), original + [fx.assistant(10, "msg_3", cr=60_000)],
                 mtime=time.time())
        sc = scan(self.root)
        self.assertEqual(sorted(c.stream.split(os.sep)[-1] for c in sc.calls),
                         ["aaaa.jsonl", "aaaa.jsonl", "bbbb.jsonl"])
        self.assertEqual(sc.dup_skipped, 2)
        self.assertEqual(sum(c.cr for c in sc.calls), 110_000)

    def test_window_uses_line_timestamp_not_mtime(self):
        # file modified "now", but one record is outside the window
        fx.write(self.root, fx.session_file(), [fx.assistant(-60 * 24 * 3, "old"), fx.assistant(0, "new")])
        sc = scan(self.root, since_epoch=epoch(-60), until_epoch=epoch(60))
        self.assertEqual([c.ts for c in sc.calls], [epoch(0)])
        self.assertEqual(sc.out_of_window, 1)

    def test_old_mtime_file_skipped(self):
        fx.write(self.root, fx.session_file(), [fx.assistant(0, "x")], mtime=epoch(-60 * 24 * 10))
        sc = scan(self.root, since_epoch=epoch(-60), until_epoch=epoch(60))
        self.assertEqual(sc.files_skipped_mtime, 1)
        self.assertEqual(sc.calls, [])

    def test_unknown_schema_is_hard_error(self):
        bad = fx.assistant(0, "m")
        bad["message"]["usage"] = {"prompt_tokens": 5, "completion_tokens": 7}
        fx.write(self.root, fx.session_file(), [bad, dict(bad, uuid="u2")])
        with self.assertRaises(SchemaError):
            scan(self.root)

    def test_schema_ignores_version_string(self):
        rec = fx.assistant(0, "m")
        rec["version"] = "123.0.0-unknown"
        fx.write(self.root, fx.session_file(), [rec])
        self.assertEqual(len(scan(self.root).calls), 1)

    def test_synthetic_model_excluded(self):
        fx.write(self.root, fx.session_file(), [fx.assistant(0, "a", model="<synthetic>"), fx.assistant(1, "b")])
        sc = scan(self.root)
        self.assertEqual(len(sc.calls), 1)
        self.assertEqual(sc.synthetic_skipped, 1)

    def test_compaction_boundaries_deduplicated(self):
        cb = fx.compact(3)
        fx.write(self.root, fx.session_file("aaaa"), [fx.assistant(0, "a"), cb], mtime=time.time() - 50)
        fx.write(self.root, fx.session_file("bbbb"), [fx.assistant(0, "a"), cb, fx.assistant(4, "b")])
        sc = scan(self.root)
        self.assertEqual(len(sc.compactions), 1)

    def test_subagent_stream_marked_side(self):
        fx.write(self.root, os.path.join(fx.SLUG, "sess", "subagents", "agent-1.jsonl"), [fx.assistant(0, "s")])
        sc = scan(self.root)
        self.assertTrue(sc.calls[0].side)


if __name__ == "__main__":
    unittest.main()
