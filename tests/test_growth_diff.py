import gc
import io
import unittest
import weakref
from contextlib import redirect_stdout

from IPython.core.error import UsageError
from IPython.core.interactiveshell import InteractiveShell

from loop_autopsy.growth_diff import _report, _snapshot


class GrowthDiffTests(unittest.TestCase):
    def test_ranking_missing_names_and_ties(self):
        before = {"gone": 512, "buffer": 20, "stable": 3, "x": 21}
        after = {"new": 256, "buffer": 30, "stable": 3, "x": 11}
        lines = _report(before, after, 4).splitlines()
        self.assertEqual(lines[1:], [
            "  gone  -512 B  (removed)", "  new  +256 B  (new)",
            "  buffer  +10 B  (grew)", "  x  -10 B  (shrank)",
        ])
        self.assertIn("Showing 2 of 4", _report(before, after, 2))

    def test_snapshot_filters_history_and_releases_objects(self):
        class Payload:
            pass

        obj = Payload()
        ref = weakref.ref(obj)
        snapshot = _snapshot({"data": obj, "_1": obj, "Out": {1: obj}})
        del obj
        gc.collect()
        self.assertIsNone(ref())
        self.assertEqual(list(snapshot), ["data"])
        self.assertIsInstance(snapshot["data"], int)

    def test_extension_in_ipython(self):
        shell = InteractiveShell()
        module = "loop_autopsy.growth_diff"
        shell.run_line_magic("load_ext", module)
        self.addCleanup(lambda: shell.run_line_magic("unload_ext", module) if shell.find_line_magic("growthdiff") else None)

        def run(line):
            output = io.StringIO()
            with redirect_stdout(output):
                shell.run_line_magic("growthdiff", line)
            return output.getvalue()

        with self.assertRaises(UsageError):
            run("show")
        shell.user_ns["samples"] = bytearray(100)
        run("mark")
        shell.user_ns["samples"].extend(bytes(4096))
        first = run("show 1")
        self.assertIn("samples", first)
        self.assertIn("(grew)", first)
        self.assertEqual(run(""), first)
        run("mark")
        self.assertIn("No estimated variable sizes changed", run("show"))
        for invalid in ("show 0", "show many", "mark extra"):
            with self.assertRaises(UsageError):
                run(invalid)
        run("clear")
        with self.assertRaises(UsageError):
            run("show")
        shell.run_line_magic("unload_ext", module)
        self.assertIsNone(shell.find_line_magic("growthdiff"))
