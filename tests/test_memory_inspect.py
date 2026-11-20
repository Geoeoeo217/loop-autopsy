import io
import unittest
from contextlib import redirect_stdout

from IPython.core.error import UsageError
from IPython.core.interactiveshell import InteractiveShell

from loop_autopsy.memory_inspect import _parts, load_ipython_extension


class MemoryInspectTests(unittest.TestCase):
    def setUp(self):
        self.ip = InteractiveShell()
        module = "loop_autopsy.memory_inspect"
        self.ip.run_line_magic("load_ext", module)
        self.addCleanup(self.ip.run_line_magic, "unload_ext", module)

    def run_magic(self, cmd, args):
        output = io.StringIO()
        with redirect_stdout(output):
            self.ip.run_line_magic(cmd, args)
        return output.getvalue()

    def test_aliases_compare_identity_and_include_output(self):
        data = bytearray(20)
        self.ip.user_ns.update(original=data, alias=data, copy=data[:])
        self.ip.user_ns["Out"][91] = data
        result = self.run_magic("memaliases", "original")
        self.assertIn("Notebook names: alias, original\n", result)
        self.assertIn("Out[91]", result)
        self.assertNotIn("copy", result)

    def test_breakdown_by_key(self):
        self.ip.user_ns["batch"] = {"metadata": "train", "pixels": bytearray(8192)}
        result = self.run_magic("memparts", "batch 1")
        self.assertIn("batch['pixels']", result)
        self.assertNotIn("metadata", result)

    def test_sample_boundary_and_ties(self):
        data = [b"x"] * 64 + [bytes(10000)]
        self.assertEqual(_parts("x", data, 1).splitlines(), [
            "Inspected 64 of 65 entries in x (estimated sizes):",
            _parts("x", (b"x",), 1).splitlines()[1],
        ])
        self.assertIn("x[0]", _parts("x", data, 1))
        self.assertEqual(_parts("x", {}, 1), "x is empty.")

    def test_opaque_key(self):
        class Key:
            def __repr__(self):
                raise AssertionError("must not format arbitrary keys")

        self.assertIn("entry #1", _parts("x", {Key(): "hello"}, 1))

    def test_invalid_inputs(self):
        self.ip.user_ns["data"] = []
        for cmd, arg in (("memaliases", "data.pop()"), ("memparts", "missing"),
                         ("memparts", "data -1"), ("memparts", "data many")):
            with self.subTest(cmd=cmd, arg=arg), self.assertRaises(UsageError):
                self.run_magic(cmd, arg)
        with self.assertRaises(UsageError):
            _parts("x", "some text", 3)

    def test_name_conflict_leaves_first_command_available(self):
        shell = InteractiveShell()
        shell.register_magic_function(lambda line: None, "line", "memparts")
        with self.assertRaises(UsageError):
            load_ipython_extension(shell)
        self.assertIsNone(shell.find_line_magic("memaliases"))
