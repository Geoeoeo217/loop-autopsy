import io
import unittest
from contextlib import redirect_stdout

from IPython.core.error import UsageError
from IPython.core.interactiveshell import InteractiveShell

from loop_autopsy.growth_check import check_source


class GrowthCheckTests(unittest.TestCase):
    def test_methods_and_conditional_update(self):
        source = ("for n in items:\n    if n:\n        items.append(n)\n"
                  "    items.extend([n])\n    items.insert(0, n)\n    items += [n]")
        self.assertEqual(check_source(source), [
            (1, 3, "items", "append()"), (1, 4, "items", "extend()"),
            (1, 5, "items", "insert()"), (1, 6, "items", "+="),
        ])

    def test_copy_separate_destination_and_else(self):
        self.assertEqual(check_source(
            "for x in items[:]:\n    items.append(x)\n"
            "for x in items:\n    results.append(x)\nelse:\n    items.append(0)"
        ), [])

    def test_nested_loops_and_function_scope(self):
        source = ("for a in aa:\n    def later(aa):\n        aa.append(1)\n"
                  "    for b in bb:\n        aa.append(b)\n        bb.extend([a])")
        self.assertEqual(check_source(source),
                         [(1, 5, "aa", "append()"), (4, 6, "bb", "extend()")])

    def test_real_cell_magic_never_runs_the_body(self):
        shell = InteractiveShell()
        module = "loop_autopsy.growth_check"
        shell.run_line_magic("load_ext", module)
        try:
            shell.user_ns["items"] = []
            stream = io.StringIO()
            with redirect_stdout(stream):
                shell.run_cell_magic("growthcheck", "",
                                     "items.append(1)\nfor x in items:\n    items.append(x)")
            self.assertEqual(shell.user_ns["items"], [])
            self.assertIn("Line 3 uses append()", stream.getvalue())
            with self.assertRaises(UsageError):
                shell.run_cell_magic("growthcheck", "", "for x in :")
        finally:
            shell.run_line_magic("unload_ext", module)
        self.assertIsNone(shell.find_cell_magic("growthcheck"))
