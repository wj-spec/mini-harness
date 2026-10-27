import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.getenv("RUN_BROWSER_SMOKE") != "1", reason="opt-in real browser smoke")
def test_real_browser_observation_smoke():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "browser_smoke.py")],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "B1 smoke: PASS" in result.stdout
