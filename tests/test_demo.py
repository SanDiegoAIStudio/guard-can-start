"""The demo must print each labelled outcome and exit 0 only when all of them match."""

import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "examples", "demo.sh")
SHELL = os.environ.get("CHECK_GUARD_SHELL", "/bin/sh")


class DemoTest(unittest.TestCase):
    def test_demo_prints_the_four_parts(self):
        """source: the demo exits 0 only when the working guard, the six breaks, the failed list, and the empty list each print the expected line."""
        proc = subprocess.run(
            [SHELL, DEMO],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30,
        )
        self.assertEqual(proc.stderr, "")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        for needle in (
            "part 1: working guard",
            "OK:",
            "part 2: broken six ways",
            "MISSING:",
            "DOES NOT PARSE:",
            "NOT EXECUTABLE:",
            "COULD NOT RUN:",
            "SAID YES TO KNOWN-BAD INPUT:",
            "SAID NO TO KNOWN-GOOD INPUT:",
            "part 3: list with one broken entry",
            "checked 3 guards: 2 ok, 1 failed",
            "part 4: list with no entry",
            "checked nothing:",
        ):
            self.assertIn(needle, proc.stdout)
        self.assertNotIn("demo: a part did not match", proc.stdout)

    def test_sample_guard_refuses_an_input_it_cannot_read(self):
        """source: the sample guard refuses an input it cannot read, and still refuses a secret and accepts a clean file."""
        guard = os.path.join(ROOT, "examples", "no-secrets.sh")
        bad = os.path.join(ROOT, "examples", "fixtures", "bad.txt")
        good = os.path.join(ROOT, "examples", "fixtures", "good.txt")
        missing = os.path.join(ROOT, "examples", "no-such-input.txt")
        folder = os.path.join(ROOT, "examples", "fixtures")

        def run(target):
            proc = subprocess.run(
                [SHELL, guard, target],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=10,
            )
            return proc.returncode, proc.stdout, proc.stderr

        code, out, err = run(missing)
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertEqual(out, "refused: cannot read the input\n")
        code, out, err = run(folder)
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertEqual(out, "refused: cannot read the input\n")
        code, out, err = run(bad)
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertEqual(out, "refused: line 2 holds a secret\n")
        code, out, err = run(good)
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
