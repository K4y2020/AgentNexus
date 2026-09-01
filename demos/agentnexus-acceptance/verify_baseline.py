#!/usr/bin/env python3
"""Run the acceptance sample's baseline suite and print a machine-readable report.

This is the evidence command for every acceptance task in this demo: agents
run it after changing ``sample_repo``, reviewers run it before signing a diff,
and human alpha/beta sessions record its output URL/session in the acceptance
matrix. It uses only the standard library, so it runs in any recent Python
without installing dependencies.

Exit code 0 means the suite ran and every test passed; any failure/error
returns 1 so automation (Workflow verify steps, CI, a Review agent) can gate on
it.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    sample = root / "sample_repo"
    sys.path.insert(0, str(sample))

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(sample / "tests"), top_level_dir=str(sample))
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    result = runner.run(suite)

    report = {
        "sample": "agentnexus-acceptance",
        "command": "verify_baseline.py",
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "success": result.testsRun > 0 and not result.failures and not result.errors,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["success"]:
        return 0
    print(stream.getvalue())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
