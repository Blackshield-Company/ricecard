import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

GLYPHSMITH = HERE.parents[2]
THEMES_DIR = GLYPHSMITH / "themes"
THEMES = ["blackshield", "neon-horizon"]


@pytest.fixture(params=THEMES)
def theme(request):
    d = THEMES_DIR / request.param
    if not (d / "colors.toml").is_file():
        pytest.skip(f"theme {request.param} not found at {d}")
    return d
