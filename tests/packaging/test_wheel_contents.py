from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _build_distributions(output_dir: Path) -> tuple[Path, Path]:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(
        [
            str(Path(sys.executable).parent),
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--outdir",
            str(output_dir),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return next(output_dir.glob("*.whl")), next(output_dir.glob("*.tar.gz"))


def test_built_distributions_contain_runtime_assets_and_license(tmp_path: Path) -> None:
    wheel, sdist = _build_distributions(tmp_path)

    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
        wheel_license_name = next(
            name for name in wheel_names if name.endswith(".dist-info/licenses/LICENSE")
        )
        wheel_license = archive.read(wheel_license_name).decode("utf-8")
    assert "agos/web/static/index.html" in wheel_names
    assert "agos/hooks/templates/pre-commit.sh" in wheel_names
    assert "agos/hooks/templates/pre-push.sh" in wheel_names
    assert "Permission is hereby granted, free of charge" in wheel_license

    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = set(archive.getnames())
        license_name = next(name for name in sdist_names if name.endswith("/LICENSE"))
        extracted = archive.extractfile(license_name)
        assert extracted is not None
        sdist_license = extracted.read().decode("utf-8")
    assert any(name.endswith("/agos/web/static/index.html") for name in sdist_names)
    assert "Permission is hereby granted, free of charge" in sdist_license


def test_read_only_release_verifier_accepts_matching_tag_and_rejects_mismatch(
    tmp_path: Path,
) -> None:
    _build_distributions(tmp_path)
    verifier = PROJECT_ROOT / "scripts" / "verify_release.py"

    valid = subprocess.run(
        [sys.executable, str(verifier), "--tag", "v0.1.0", "--dist", str(tmp_path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    mismatch = subprocess.run(
        [sys.executable, str(verifier), "--tag", "v9.9.9", "--dist", str(tmp_path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert "release verification passed" in valid.stdout
    assert mismatch.returncode == 1
    assert "does not match project version" in mismatch.stderr
