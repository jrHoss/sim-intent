# Ensures the repo root is on sys.path so `ir`, `geom`, etc. import from tests.
#
# Task 18: the automated suite runs in the explicit `test` runtime mode so the
# frozen REPLAY fallback contract tests keep exercising their routes. This is
# set before any application module import; the new runtime-mode matrix tests
# construct applications with explicit modes instead of mutating this value.
import os

os.environ["SIM_INTENT_MODE"] = "test"
