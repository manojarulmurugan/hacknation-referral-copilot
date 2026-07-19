"""Build the minimal repo-root bundle consumed by Databricks Apps."""

from __future__ import annotations

import argparse
import math
import shutil
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "build" / "databricks_app"

PIPELINE_FILES = [
    "__init__.py",
    "stage3_provenance_audit.py",
    "stage4_taxonomy_mapping.py",
    "stage5_readiness_scoring.py",
]
DATA_FILES = [
    "facilities_local.parquet",
    "processed/facility_capability_readiness.parquet",
    "processed/bullet_capability_map.parquet",
    "processed/evidence_bullets.parquet",
]
TAXONOMY_FILES = [
    "capability_taxonomy.yaml",
    "normalization_vocab.json",
]
MAX_WORKSPACE_FILE_BYTES = 9 * 1024 * 1024


def copy_file(relative_path: str, source_root: Path, output_root: Path) -> None:
    source = source_root / relative_path
    if not source.exists():
        raise FileNotFoundError(f"Required deployment file is missing: {source}")
    destination = output_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_data_artifact(relative_path: str, output_root: Path) -> None:
    source = ROOT / "data" / relative_path
    if source.stat().st_size <= MAX_WORKSPACE_FILE_BYTES:
        copy_file(relative_path, ROOT / "data", output_root / "data")
        return

    frame = pd.read_parquet(source)
    part_count = max(2, math.ceil(source.stat().st_size / (7 * 1024 * 1024)))
    destination = output_root / "data" / Path(relative_path).with_suffix("")
    while True:
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        rows_per_part = math.ceil(len(frame) / part_count)
        paths = []
        for index, start in enumerate(range(0, len(frame), rows_per_part)):
            path = destination / f"part-{index:03d}.parquet"
            frame.iloc[start : start + rows_per_part].to_parquet(path, index=False)
            paths.append(path)
        if max(path.stat().st_size for path in paths) <= MAX_WORKSPACE_FILE_BYTES:
            break
        part_count += 1


def build_bundle(output: Path) -> int:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    shutil.copytree(
        ROOT / "app",
        output / "app",
        ignore=shutil.ignore_patterns("__pycache__", ".databricks", "*.pyc", "*.sqlite"),
    )
    for name in PIPELINE_FILES:
        copy_file(name, ROOT / "pipeline", output / "pipeline")
    for name in DATA_FILES:
        copy_data_artifact(name, output)
    for name in TAXONOMY_FILES:
        copy_file(name, ROOT / "taxonomy", output / "taxonomy")
    for name in ["app.yaml", "requirements.txt"]:
        copy_file(name, ROOT, output)

    total_bytes = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    print(f"Built {output} ({total_bytes / 1024 / 1024:.1f} MiB)")
    return total_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build_bundle(args.output.resolve())


if __name__ == "__main__":
    main()
