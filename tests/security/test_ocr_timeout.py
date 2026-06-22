from __future__ import annotations

import subprocess

from PIL import Image

from sliderefine.legacy_pipeline import WINDOWS_OCR_TIMEOUT_SECONDS, detect_windows_ocr_candidates


def test_windows_ocr_subprocess_timeout_falls_back(monkeypatch):
    observed_timeout = None

    def fake_run(*args, **kwargs):
        nonlocal observed_timeout
        observed_timeout = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=observed_timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    candidates = detect_windows_ocr_candidates(Image.new("RGBA", (8, 8), "white"))

    assert candidates == []
    assert observed_timeout == WINDOWS_OCR_TIMEOUT_SECONDS
