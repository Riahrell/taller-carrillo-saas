import os
import subprocess
import sys
from io import BytesIO

import pytest
from PIL import Image

from app.modules.catalog import image_processing as processing
from app.modules.catalog.images import InvalidProductImage


def png():
    with Image.new("RGBA", (16, 8), (1, 2, 3, 128)) as image, BytesIO() as output:
        image.save(output, "PNG")
        return output.getvalue()


def test_real_worker_roundtrip_and_rejection():
    result = processing.process_product_image(png())
    assert (result.width, result.height) == (16, 8)
    with Image.open(BytesIO(result.content)) as image:
        assert image.getpixel((0, 0)) == (1, 2, 3, 128)
        assert image.info == {}
    with pytest.raises(InvalidProductImage):
        processing.process_product_image(b"not an image")
    assert processing.process_product_image(png()).sha256 == result.sha256


def test_busy_rejected_without_waiting_or_spawning():
    assert processing._SLOT.acquire(blocking=False)
    try:
        with pytest.raises(processing.ImageProcessingUnavailable):
            processing.process_product_image(png())
    finally:
        processing._SLOT.release()


def test_timeout_kills_and_reaps_child_then_releases_slot(monkeypatch, tmp_path):
    worker = tmp_path / "slow.py"
    pid_file = tmp_path / "pid"
    worker.write_text(
        "import os, pathlib, time\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(processing, "_WORKER", worker)
    monkeypatch.setattr(processing, "WALL_TIMEOUT_SECONDS", 0.5)
    with pytest.raises(processing.ImageProcessingUnavailable):
        processing.process_product_image(png())
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert processing._SLOT.acquire(blocking=False)
    processing._SLOT.release()


def test_child_receives_clean_environment_and_closed_descriptors(monkeypatch):
    original = subprocess.run
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-reach-child")

    def checked(*args, **kwargs):
        assert kwargs["env"] == {"LANG": "C.UTF-8"}
        assert kwargs["close_fds"] is True
        assert "-I" in args[0]
        assert kwargs["stderr"] == subprocess.DEVNULL
        return original(*args, **kwargs)

    monkeypatch.setattr(processing.subprocess, "run", checked)
    processing.process_product_image(png())


def test_limits_are_effective_in_real_child():
    root = processing._WORKER.resolve().parents[3]
    code = f"""
import sys, resource
sys.path.insert(0, {str(root)!r})
from app.modules.catalog import images, image_worker
original = images.prepare_product_image
def checked(payload):
    assert resource.getrlimit(resource.RLIMIT_AS) == (536870912, 536870912)
    assert resource.getrlimit(resource.RLIMIT_CPU) == (5, 5)
    assert resource.getrlimit(resource.RLIMIT_FSIZE) == (12582912, 12582912)
    assert resource.getrlimit(resource.RLIMIT_NOFILE) == (32, 32)
    assert resource.getrlimit(resource.RLIMIT_CORE) == (0, 0)
    return original(payload)
images.prepare_product_image = checked
raise SystemExit(image_worker.main())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        input=png(),
        capture_output=True,
        env={"LANG": "C.UTF-8"},
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(b"\x89PNG")


@pytest.mark.parametrize("payload", [b"invalid", b"x" * 100])
def test_invalid_worker_output_is_not_returned(monkeypatch, tmp_path, payload):
    worker = tmp_path / "invalid.py"
    worker.write_text(f"import sys\nsys.stdout.buffer.write({payload!r})\n")
    monkeypatch.setattr(processing, "_WORKER", worker)
    with pytest.raises(processing.ImageProcessingUnavailable):
        processing.process_product_image(png())
