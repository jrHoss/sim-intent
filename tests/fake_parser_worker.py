"""Controlled parser subprocess used by R2.1 boundary tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys


mode = sys.argv[1]
extra = sys.argv[2:-2]
kind, path = sys.argv[-2:]
digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
valid = {
    "protocol_version": 1,
    "status": "valid",
    "kind": kind,
    "inventory": {"file_sha256": digest, "source_name": "worker"},
}

if mode == "valid_stderr":
    sys.stderr.write("diagnostic" * 10000)
    print(json.dumps(valid))
elif mode == "large_stdout":
    sys.stdout.write("x" * 100000)
elif mode == "value":
    sys.stdout.write(extra[0])
elif mode == "error":
    sys.stderr.write(extra[0])
    print(json.dumps({**valid, "status": "error", "inventory": None}))
elif mode == "descendant":
    side_effect = extra[0]
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import pathlib,time,sys; time.sleep(1); pathlib.Path(sys.argv[1]).write_text('survived')",
            side_effect,
        ]
    )
    import time
    time.sleep(30)
else:
    raise SystemExit(3)
