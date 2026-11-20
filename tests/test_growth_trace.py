import io
import tracemalloc
import unittest
from contextlib import redirect_stdout

from IPython.core.error import UsageError
from IPython.core.interactiveshell import InteractiveShell


class GrowthTraceTests(unittest.TestCase):
    def setUp(self):
        self.shell = InteractiveShell()
        self.module = "loop_autopsy.extras.growth_trace"
        self.shell.run_line_magic("load_ext", self.module)
        self.addCleanup(self.shell.run_line_magic, "unload_ext", self.module)

    def magic(self, line):
        output = io.StringIO()
        with redirect_stdout(output):
            self.shell.run_line_magic("growthtrace", line)
        return output.getvalue()

    def test_attribution_in_a_real_ipython_cell(self):
        self.magic("mark")
        result = self.shell.run_cell("chunks = []\nfor i in range(4):\n    chunks.append(bytearray(65536))")
        self.assertTrue(result.success)
        for _ in range(2):
            report = self.magic("show 1")
            self.assertIn("chunks.append(bytearray(65536))", report)
            self.assertIn(":3", report)
        self.magic("mark")
        self.assertNotIn("chunks.append(bytearray(65536))", self.magic("show"))

    def test_arguments_and_stop(self):
        for args in ("show", "show 0", "show potato", "mark extra"):
            with self.subTest(args=args), self.assertRaises(UsageError):
                self.magic(args)
        already_active = tracemalloc.is_tracing()
        self.magic("mark")
        self.magic("stop")
        self.assertEqual(tracemalloc.is_tracing(), already_active)
        with self.assertRaises(UsageError):
            self.magic("show")

    def test_preserves_existing_tracer(self):
        if not tracemalloc.is_tracing():
            tracemalloc.start()
            self.addCleanup(tracemalloc.stop)
        self.magic("mark")
        self.magic("stop")
        self.assertTrue(tracemalloc.is_tracing())
