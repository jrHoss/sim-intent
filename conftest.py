# Ensures the repo root is on sys.path so `ir`, `geom`, etc. import from tests.
#
# Task 18: the automated suite runs in the explicit `test` runtime mode so the
# frozen REPLAY fallback contract tests keep exercising their routes. This is
# set before any application module import; the new runtime-mode matrix tests
# construct applications with explicit modes instead of mutating this value.
import os

os.environ["SIM_INTENT_MODE"] = "test"


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Report how many Task 19 baseline comparisons actually ran.

    Review correction B-1: these comparisons used to skip silently in every
    hosted CI job. Printing the accounting makes a silent skip visible even
    when the required-evidence flag is not set.
    """

    try:
        from tests.baseline_evidence import COUNTERS, summary_line
    except Exception:  # pragma: no cover - suite not collected from the repo
        return
    if COUNTERS.executed or COUNTERS.skipped or COUNTERS.failed:
        terminalreporter.write_line(summary_line())
