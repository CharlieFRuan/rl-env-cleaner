"""Validate the published cyber rows and freeze their image/target manifest.

This is an offline inventory, not evidence that images start or tasks are solvable.
Run with Harbor's Python (which already includes pyarrow).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(parquet: Path, mapping_path: Path) -> tuple[list[dict], dict]:
    mapping = {}
    for line in mapping_path.read_text().splitlines():
        row = json.loads(line)
        key, image = row["dataset_image"], row["dockerhub_image"]
        if key in mapping and mapping[key] != image:
            raise ValueError(f"Conflicting image mapping: {key}")
        mapping[key] = image

    manifest = []
    seen = set()
    for row in pq.read_table(parquet).to_pylist():
        instance = json.loads(row["extra_info"]["instance_json"])
        task = instance["instance_id"]
        if task in seen:
            raise ValueError(f"Duplicate task ID: {task}")
        seen.add(task)
        if row["data_source"] != "arvo" or instance["dataset_type"] != "arvo":
            raise ValueError(f"Unexpected dataset type: {task}")
        if row["extra_info"]["instance_id"] != task:
            raise ValueError(f"Inconsistent task ID: {task}")
        description = instance["description"]
        target = re.fullmatch(
            r"(?P<sanitizer>\S+):\s+(?P<error_type>\S+) in function "
            r"`(?P<function>[^`]+)` in file `(?P<file>[^`]+)`",
            description,
        )
        if not target:
            raise ValueError(f"Malformed grading description: {task}")
        image = mapping[instance["docker_image"]]  # Fail on unmapped images.
        manifest.append({
            "task_id": task,
            "dataset_index": row["extra_info"]["index"],
            "dataset_image": instance["docker_image"],
            "dockerhub_image": image,
            "cwd": instance["cwd"],
            "description": description,
            "instruction": instance["problem_statement"],
            "target": target.groupdict(),
            "project": target["file"].split("/")[0],
        })

    descriptions = Counter(m["description"] for m in manifest)
    summary = {
        "source": "https://huggingface.co/datasets/XiaomiMiMo/MiMo-V2.6-RL-oss",
        "config": "cyber",
        "inputs_sha256": {
            "cyber.parquet": sha256(parquet),
            "image-mapping.jsonl": sha256(mapping_path),
        },
        "tasks": len(manifest),
        "unique_task_ids": len(seen),
        "image_mappings_available": len(mapping),
        "unmapped_images": 0,
        "malformed_targets": 0,
        "unique_descriptions": len(descriptions),
        "repeated_description_rows": sum(n - 1 for n in descriptions.values()),
        "projects": dict(Counter(m["project"] for m in manifest).most_common()),
        "bug_types": dict(Counter(
            f'{m["target"]["sanitizer"]}: {m["target"]["error_type"]}' for m in manifest
        ).most_common()),
        "runtime_validation": "NOT RUN",
        "deduplication": "Retain distinct IDs; identical descriptions do not prove identical binaries.",
    }
    return manifest, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "mimo/cyber")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest, summary = inventory(
        args.data_dir / "cyber.parquet", args.data_dir / "image-mapping.jsonl"
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "manifest.jsonl").write_text(
        "".join(json.dumps(m, sort_keys=True) + "\n" for m in manifest)
    )
    (args.output_dir / "inventory.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in (
        "tasks", "unique_task_ids", "unmapped_images", "malformed_targets",
        "unique_descriptions", "runtime_validation",
    )}, indent=2))


if __name__ == "__main__":
    main()
