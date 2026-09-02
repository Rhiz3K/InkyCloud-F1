"""Safety and cleanup tests for the reset-db maintenance script."""

import os
import subprocess
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "reset_db.sh"


def _run_reset(*args: str, database_path: str, images_path: str, answer: str = "y\n"):
    env = os.environ.copy()
    env.update(DATABASE_PATH=database_path, IMAGES_PATH=images_path)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH), *(args or ("all",))],
        input=answer,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_reset_all_removes_database_sidecars_and_only_bmp_images(tmp_path):
    database_path = tmp_path / "data" / "f1.db"
    database_path.parent.mkdir()
    images_path = tmp_path / "images"
    images_path.mkdir()

    for path in (
        database_path,
        Path(f"{database_path}-wal"),
        Path(f"{database_path}-shm"),
        images_path / "calendar.bmp",
    ):
        path.write_bytes(b"test")
    retained_png = images_path / "preview.png"
    retained_png.write_bytes(b"png")

    result = _run_reset(
        "all",
        "--force",
        database_path=str(database_path),
        images_path=str(images_path),
    )

    assert result.returncode == 0
    assert not database_path.exists()
    assert not Path(f"{database_path}-wal").exists()
    assert not Path(f"{database_path}-shm").exists()
    assert not (images_path / "calendar.bmp").exists()
    assert retained_png.exists()


def test_reset_refuses_root_paths(tmp_path):
    database_path = tmp_path / "f1.db"
    database_path.write_bytes(b"test")

    images_root = _run_reset(database_path=str(database_path), images_path="/")
    database_root = _run_reset(database_path="/", images_path=str(tmp_path))

    assert images_root.returncode == 1
    assert database_root.returncode == 1
    assert "Refusing to run" in images_root.stderr
    assert "Refusing to run" in database_root.stderr
    assert database_path.exists()


def test_reset_all_refuses_while_wal_sidecar_indicates_open_connection(tmp_path):
    database_path = tmp_path / "f1.db"
    database_path.write_bytes(b"test")
    Path(f"{database_path}-wal").write_bytes(b"wal")

    result = _run_reset(database_path=str(database_path), images_path=str(tmp_path))

    assert result.returncode == 1
    assert "appears to be in use" in result.stderr
    assert database_path.exists()


def test_reset_rejects_unknown_arguments(tmp_path):
    result = _run_reset("bogus", database_path=str(tmp_path / "f1.db"), images_path=str(tmp_path))

    assert result.returncode == 1
    assert "Usage: reset-db" in result.stdout
