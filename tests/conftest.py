from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw


@pytest.fixture()
def fixture_image(tmp_path: Path) -> Path:
    path = tmp_path / "shape-heavy.png"
    image = Image.new("RGB", (180, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 16, 92, 68), fill="#2f80ed")
    draw.ellipse((104, 18, 160, 74), fill="#27ae60")
    draw.rectangle((20, 82, 160, 100), fill="#111111")
    image.save(path)
    return path
