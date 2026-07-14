#!/usr/bin/env python3
"""Read-only verification for AGOS release metadata and distributions."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys
import tarfile
import tomllib
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LICENSE_SENTENCE = "Permission is hereby granted, free of charge"


def _project_version() -> str:
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    metadata_version = str(metadata["project"]["version"])
    source_path = PROJECT_ROOT / "src" / "agos" / "__init__.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    source_versions = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if source_versions != [metadata_version]:
        raise ValueError(
            "src/agos/__init__.py __version__ does not match project version "
            f"{metadata_version!r}"
        )
    return metadata_version


def _single_distribution(dist_dir: Path, pattern: str, label: str) -> Path:
    matches = sorted(dist_dir.glob(pattern))
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {label} in {dist_dir}, found {len(matches)}"
        )
    return matches[0]


def _verify_wheel(wheel: Path, version: str) -> None:
    expected_prefix = f"agos-{version}-"
    if not wheel.name.startswith(expected_prefix):
        raise ValueError(f"wheel name does not match project version {version}: {wheel.name}")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        required = {
            "agos/web/static/index.html",
            "agos/hooks/templates/pre-commit.sh",
            "agos/hooks/templates/pre-push.sh",
        }
        missing = sorted(required - names)
        if missing:
            raise ValueError(f"wheel is missing runtime assets: {', '.join(missing)}")
        license_names = [
            name for name in names if name.endswith(".dist-info/licenses/LICENSE")
        ]
        if len(license_names) != 1:
            raise ValueError("wheel must contain exactly one dist-info/licenses/LICENSE")
        license_text = archive.read(license_names[0]).decode("utf-8")
        if LICENSE_SENTENCE not in license_text:
            raise ValueError("wheel contains an incomplete MIT license")


def _verify_sdist(sdist: Path, version: str) -> None:
    expected_name = f"agos-{version}.tar.gz"
    if sdist.name != expected_name:
        raise ValueError(f"sdist name does not match project version {version}: {sdist.name}")
    with tarfile.open(sdist, "r:gz") as archive:
        names = set(archive.getnames())
        license_names = [name for name in names if name.endswith("/LICENSE")]
        if len(license_names) != 1:
            raise ValueError("sdist must contain exactly one LICENSE")
        license_file = archive.extractfile(license_names[0])
        if license_file is None:
            raise ValueError("sdist LICENSE cannot be read")
        license_text = license_file.read().decode("utf-8")
        if LICENSE_SENTENCE not in license_text:
            raise ValueError("sdist contains an incomplete MIT license")
        if not any(name.endswith("/agos/web/static/index.html") for name in names):
            raise ValueError("sdist is missing the Dashboard runtime asset")


def verify_release(*, dist_dir: Path, tag: str | None) -> str:
    version = _project_version()
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"tag {tag!r} does not match project version {version!r}")
    if not dist_dir.is_dir():
        raise ValueError(f"distribution directory does not exist: {dist_dir}")
    wheel = _single_distribution(dist_dir, "*.whl", "wheel")
    sdist = _single_distribution(dist_dir, "*.tar.gz", "sdist")
    _verify_wheel(wheel, version)
    _verify_sdist(sdist, version)
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--tag")
    args = parser.parse_args(argv)
    try:
        version = verify_release(dist_dir=args.dist, tag=args.tag)
    except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"release verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"release verification passed: agos {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
