import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.getenv("RUN_BROWSER_SMOKE") != "1" or os.name != "posix",
                    reason="opt-in POSIX PTY + real browser smoke")
def test_ink_browser_approval_smoke():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/browser_ink_smoke.py")],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "B2 Ink smoke: PASS" in result.stdout
    assert "deterministic local model stub" in result.stdout
