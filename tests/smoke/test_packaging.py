import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed")
def test_docker_image_serves_health():
    result = subprocess.run(
        ["bash", "scripts/smoke_docker.sh"], cwd=ROOT, capture_output=True, text=True, timeout=1200
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "smoke: ok" in result.stdout
