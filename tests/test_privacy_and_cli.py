import ast
import contextlib
import io
import json
import os
import tempfile
import unittest

from spend_teardown import cli
from spend_teardown.export import PrivacyError, check_private
from tests import fixtures as fx

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spend_teardown")
NETWORK_MODULES = {"socket", "ssl", "http", "urllib", "urllib2", "urllib3", "requests", "httpx", "aiohttp",
                   "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc", "asyncio", "subprocess",
                   "websocket", "websockets", "grpc", "paramiko"}


def _rd(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _jl(path):
    return json.loads(_rd(path))


def _wr(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def build_world(root):
    fx.write(root, fx.session_file(), [
        fx.user(0),
        fx.assistant(0, "a", cw1h=120_000),
        fx.assistant(2, "b", cr=120_000),
        fx.compact(30, "auto"),
        fx.assistant(31, "c", cw1h=80_000),
        fx.assistant(200, "d", cw1h=90_000),
    ])
    fx.write(root, os.path.join(fx.SLUG, "3f2a9c1e-0000-4000-8000-000000000001", "subagents", "agent-x.jsonl"),
             [fx.assistant(3, "s1", model="claude-opus-5-5", cw1h=70_000, sidechain=True)])


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "projects")
        build_world(self.root)
        self.out = os.path.join(self.tmp.name, "export.json")
        self.salt = os.path.join(self.tmp.name, "salt")

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, *extra):
        return run_cli(["scan", "--root", self.root, "--since", "2026-11-01", "--until", "2026-11-10",
                        "--tz", "UTC", "--out", self.out, "--salt-file", self.salt, *extra])

    def test_export_has_no_paths_names_ids_or_times(self):
        code, _out, err = self.scan()
        self.assertEqual(code, 0, err)
        raw = _rd(self.out)
        for needle in ("/Users/", "-Users-", "alice", "acmecorp", "billing", "3f2a9c1e", "agent-x",
                       "SECRET", "T09:", ":00Z", fx.SLUG):
            self.assertNotIn(needle, raw, needle)
        with open(self.salt, encoding="utf-8") as fh:
            self.assertNotIn(fh.read().strip(), raw)
        export = json.loads(raw)
        self.assertEqual(export["window"], {"since": "2026-11-01", "until": "2026-11-10", "tz": "UTC"})
        self.assertTrue(all(len(r["day"]) == 10 for r in export["by_day"]))
        self.assertTrue(export["by_project"][0]["project"].startswith("p_"))

    def test_salted_hash_stable_per_machine_different_across_salts(self):
        self.scan()
        h1 = _jl(self.out)["by_project"][0]["project"]
        self.scan()
        self.assertEqual(h1, _jl(self.out)["by_project"][0]["project"])
        os.remove(self.salt)
        self.scan()
        self.assertNotEqual(h1, _jl(self.out)["by_project"][0]["project"])

    def test_check_private_rejects_leaks(self):
        base = {"schema": "x", "by_day": [], "pricing": {}}
        for bad in ({"x": "/Users/bob/repo"}, {"x": "-Users-bob-repo"}, {"x": "2026-11-03T09:00:00Z"},
                    {"x": "at 14:05"}, {"x": "3f2a9c1e-0000-4000-8000-000000000001"}):
            with self.assertRaises(PrivacyError, msg=str(bad)):
                check_private(dict(base, **bad))
        with self.assertRaises(PrivacyError):
            check_private(dict(base, x="acmecorp numbers"), forbidden=["acmecorp"])

    def test_show_export_prints_json(self):
        code, out, _ = self.scan("--show-export")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["schema"], "spend-teardown/export/v1")

    def test_merge_and_report(self):
        self.scan()
        second = os.path.join(self.tmp.name, "export2.json")
        os.replace(self.out, second)
        self.scan()
        team = os.path.join(self.tmp.name, "team.json")
        code, out, err = run_cli(["merge", self.out, second, "-o", team])
        self.assertEqual(code, 0, err)
        t = _jl(team)
        one = _jl(self.out)
        self.assertEqual(t["members"], 2)
        self.assertEqual(t["totals"]["calls"], 2 * one["totals"]["calls"])
        self.assertAlmostEqual(t["projection"]["projected_savings_pct"], one["projection"]["projected_savings_pct"], 1)
        code, out, _ = run_cli(["report", team])
        self.assertEqual(code, 0)
        self.assertIn("Projected saving", out)

    def test_merge_rejects_unknown_schema(self):
        bad = os.path.join(self.tmp.name, "bad.json")
        _wr(bad, json.dumps({"schema": "other/v9"}))
        code, _, err = run_cli(["merge", bad, "-o", os.path.join(self.tmp.name, "t.json")])
        self.assertEqual(code, cli.EXIT_SCHEMA)

    def test_schema_error_exit_code(self):
        bad = fx.assistant(5, "zz")
        bad["message"]["usage"] = {"tokens": 1}
        fx.write(self.root, os.path.join(fx.SLUG, "other.jsonl"), [bad] * 3)
        root2 = os.path.join(self.tmp.name, "only-bad")
        fx.write(root2, os.path.join(fx.SLUG, "x.jsonl"), [bad, dict(bad, uuid="2")])
        code, _, err = run_cli(["scan", "--root", root2, "--since", "2026-11-01", "--until", "2026-11-10",
                                "--tz", "UTC", "--out", self.out, "--salt-file", self.salt])
        self.assertEqual(code, cli.EXIT_SCHEMA, err)
        self.assertIn("unknown transcript schema", err)


class NoNetworkTests(unittest.TestCase):
    def test_package_imports_no_network_modules(self):
        offenders = []
        for dirpath, _dirs, files in os.walk(PKG):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                tree = ast.parse(_rd(path), path)
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                        names = [node.module]
                    elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "__import__":
                        offenders.append("%s: dynamic __import__" % fn)
                    for n in names:
                        if n.split(".")[0] in NETWORK_MODULES:
                            offenders.append("%s: %s" % (fn, n))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
