"""Conservative, copy-on-write repair planning and verification.

The P0 repair engine intentionally supports only values whose result can be
derived uniquely from existing, readable metadata.  It never edits payload
rows, timestamps, media, actions, states, or episode boundaries.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

from openbot_data import __version__
from openbot_data.diff import diff_dataset_snapshots
from openbot_data.errors import DatasetArgumentError, DatasetNotFoundError
from openbot_data.models import DatasetSnapshot
from openbot_data.preflight import (
    audit_dataset,
    dataset_fingerprint,
    prepare_dataset,
)
from openbot_data.serialization import write_json_atomic
from openbot_data.snapshot import build_dataset_snapshot
from openbot_data.video import VIDEO_EXTENSIONS

REPAIR_PLAN_SCHEMA_VERSION = "openbot.dataset_repair_plan.v1"
REPAIR_PLAN_FINGERPRINT_VERSION = "openbot.dataset_repair_plan.fingerprint.v1"
REPAIR_RECEIPT_SCHEMA_VERSION = "openbot.dataset_repair_receipt.v1"
REPAIR_RECEIPT_FINGERPRINT_VERSION = (
    "openbot.dataset_repair_receipt.fingerprint.v1"
)

_INFO_ARTIFACT = "meta/info.json"
_ALLOWED_INFO_POINTERS = {
    "/total_episodes": "total_episodes",
    "/total_frames": "total_frames",
    "/total_tasks": "total_tasks",
    "/total_videos": "total_videos",
}
_ADDRESSED_FINDINGS = {
    "total_episodes": {"LEROBOT_EPISODE_COUNT_MISMATCH"},
}
_PLAN_REQUIRED_KEYS = {
    "schema_version",
    "fingerprint_version",
    "status",
    "source",
    "snapshot_options",
    "source_snapshot",
    "source_audit",
    "steps",
    "unresolved_findings",
    "safety",
    "tool_versions",
    "plan_fingerprint",
}
_STEP_REQUIRED_KEYS = {
    "id",
    "operation",
    "artifact",
    "json_pointer",
    "before_exists",
    "before",
    "after",
    "derivation",
    "fixability",
    "risk",
    "preconditions",
    "finding_codes",
}
_DERIVATION_METHODS = {
    "count_unique_episode_records",
    "count_parquet_rows",
    "sum_validated_episode_lengths",
    "count_unique_task_mapping",
    "count_unique_episode_task_names",
    "count_local_video_files",
}

PlanValue = Union[Mapping[str, Any], str, Path]
LoaderRunner = Callable[[str], Any]


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _read_json_object(path: Path, *, label: str) -> Dict[str, Any]:
    try:
        loaded = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise DatasetArgumentError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(loaded, dict):
        raise DatasetArgumentError(f"{label} must be a JSON object: {path}")
    return loaded


def _json_safe(value: Any, *, source_root: Optional[Path] = None) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str) and source_root is not None:
            return value.replace(str(source_root), ".")
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, Path):
        return _json_safe(str(value), source_root=source_root)
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, source_root=source_root)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0]).encode("utf-8"),
            )
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, source_root=source_root) for item in value]
    return _json_safe(str(value), source_root=source_root)


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                value,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DatasetArgumentError(
            "Derived metadata cannot be serialized as finite canonical JSON"
        ) from exc


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _path_exists(path: Path) -> bool:
    return os.path.lexists(str(path))


def _same_json_value(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's bool/int/float coercion."""
    return type(left) is type(right) and left == right


def _publish_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically rename a staged directory without replacing a raced target."""
    if sys.platform == "darwin":
        library = ctypes.CDLL(None, use_errno=True)
        rename_exclusive = getattr(library, "renamex_np", None)
        if rename_exclusive is None:
            raise DatasetArgumentError(
                "Atomic no-replace publishing is unavailable on this platform"
            )
        rename_exclusive.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(
            os.fsencode(source),
            os.fsencode(destination),
            0x00000004,
        )
    elif sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        rename_no_replace = getattr(library, "renameat2", None)
        if rename_no_replace is None:
            raise DatasetArgumentError(
                "Atomic no-replace publishing is unavailable on this platform"
            )
        rename_no_replace.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_no_replace.restype = ctypes.c_int
        result = rename_no_replace(
            -100,
            os.fsencode(source),
            -100,
            os.fsencode(destination),
            0x00000001,
        )
    elif os.name == "nt":  # pragma: no cover - Windows CI is not available
        try:
            os.rename(source, destination)
        except FileExistsError as exc:
            raise DatasetArgumentError(
                f"Repair output appeared during staging: {destination}"
            ) from exc
        return
    else:  # pragma: no cover - fail closed on unsupported platforms
        raise DatasetArgumentError(
            "Atomic no-replace publishing is unavailable on this platform"
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise DatasetArgumentError(
            f"Repair output appeared during staging: {destination}"
        )
    raise OSError(
        error_number,
        os.strerror(error_number),
        str(destination),
    )


def _hash_record(
    digest: Any,
    kind: bytes,
    relative: str,
    payload_length: int,
) -> None:
    encoded = relative.encode("utf-8")
    digest.update(kind)
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    digest.update(payload_length.to_bytes(16, "big"))


def _tree_sha256(
    root: Path,
    *,
    overlays: Optional[Mapping[str, bytes]] = None,
) -> str:
    """Hash paths, entry kinds, symlink targets, empty dirs, and file bytes."""
    if not root.is_dir():
        raise DatasetNotFoundError(f"Directory not found: {root}")
    overlay_values = dict(overlays or {})
    digest = hashlib.sha256()
    digest.update(b"openbot.dataset-tree.sha256.v1\0")

    def visit(directory: Path, relative_directory: str) -> None:
        try:
            with os.scandir(directory) as scanner:
                entries = sorted(
                    scanner,
                    key=lambda item: item.name.encode("utf-8"),
                )
        except OSError as exc:
            raise DatasetArgumentError(
                f"Dataset tree could not be read: {directory}"
            ) from exc
        for entry in entries:
            relative = (
                f"{relative_directory}/{entry.name}"
                if relative_directory
                else entry.name
            )
            entry_path = Path(entry.path)
            if entry.is_symlink():
                try:
                    target = os.readlink(entry.path).encode("utf-8")
                except OSError as exc:
                    raise DatasetArgumentError(
                        f"Dataset symlink could not be read: {relative}"
                    ) from exc
                _hash_record(digest, b"L", relative, len(target))
                digest.update(target)
                continue
            if entry.is_dir(follow_symlinks=False):
                _hash_record(digest, b"D", relative, 0)
                visit(entry_path, relative)
                continue
            if not entry.is_file(follow_symlinks=False):
                raise DatasetArgumentError(
                    f"Unsupported special file in dataset: {relative}"
                )
            replacement = overlay_values.get(relative)
            if replacement is not None:
                _hash_record(digest, b"F", relative, len(replacement))
                digest.update(replacement)
                continue
            try:
                size = entry.stat(follow_symlinks=False).st_size
                _hash_record(digest, b"F", relative, size)
                with entry_path.open("rb") as source:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
            except OSError as exc:
                raise DatasetArgumentError(
                    f"Dataset file could not be hashed: {relative}"
                ) from exc

    visit(root, "")
    if overlay_values:
        unknown_overlays = sorted(
            set(overlay_values) - _regular_file_paths(root)
        )
        if unknown_overlays:
            raise DatasetArgumentError(
                "Tree hash overlay refers to a missing file: "
                f"{unknown_overlays[0]}"
            )
    return digest.hexdigest()


def _regular_file_paths(root: Path) -> set[str]:
    paths: set[str] = set()
    for directory, directory_names, file_names in os.walk(
        root,
        followlinks=False,
    ):
        directory_names.sort()
        file_names.sort()
        base = Path(directory)
        for file_name in file_names:
            candidate = base / file_name
            if candidate.is_file() and not candidate.is_symlink():
                paths.add(candidate.relative_to(root).as_posix())
    return paths


def _read_jsonl_records(
    paths: Sequence[Path],
    root: Path,
) -> Tuple[list[Dict[str, Any]], list[str]]:
    records: list[Dict[str, Any]] = []
    problems: list[str] = []
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            problems.append(f"{relative}:unreadable")
            continue
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line, parse_constant=_reject_json_constant)
            except (json.JSONDecodeError, ValueError):
                problems.append(f"{relative}:{line_number}:invalid_json")
                continue
            if not isinstance(value, dict):
                problems.append(f"{relative}:{line_number}:not_object")
                continue
            records.append(value)
    return records, problems


def _read_parquet_records(
    paths: Sequence[Path],
    root: Path,
) -> Tuple[list[Dict[str, Any]], list[str]]:
    if not paths:
        return [], []
    try:
        import pyarrow.parquet as parquet  # type: ignore[import-not-found,import-untyped]
    except ImportError:
        return [], ["pyarrow_dependency_missing"]
    records: list[Dict[str, Any]] = []
    problems: list[str] = []
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        try:
            table = parquet.read_table(path)
            records.extend(
                dict(row)
                for row in table.to_pylist()
                if isinstance(row, dict)
            )
        except Exception as exc:  # pragma: no cover - dependency error variants
            problems.append(f"{relative}:unreadable:{type(exc).__name__}")
    return records, problems


def _metadata_records(
    root: Path,
    name: str,
) -> Tuple[list[Dict[str, Any]], list[str], list[str]]:
    meta = root / "meta"
    jsonl_paths: list[Path] = []
    direct_jsonl = meta / f"{name}.jsonl"
    if direct_jsonl.is_file():
        jsonl_paths.append(direct_jsonl)
    nested = meta / name
    if nested.is_dir():
        jsonl_paths.extend(nested.rglob("*.jsonl"))
    parquet_paths: list[Path] = []
    direct_parquet = meta / f"{name}.parquet"
    if direct_parquet.is_file():
        parquet_paths.append(direct_parquet)
    if nested.is_dir():
        parquet_paths.extend(nested.rglob("*.parquet"))
    jsonl_paths = sorted(set(jsonl_paths))
    parquet_paths = sorted(set(parquet_paths))
    paths = [
        path.relative_to(root).as_posix()
        for path in sorted(jsonl_paths + parquet_paths)
    ]
    if jsonl_paths and parquet_paths:
        return [], paths, [f"{name}:mixed_jsonl_parquet_layout"]
    jsonl_records, jsonl_problems = _read_jsonl_records(jsonl_paths, root)
    parquet_records, parquet_problems = _read_parquet_records(parquet_paths, root)
    return (
        jsonl_records + parquet_records,
        paths,
        jsonl_problems + parquet_problems,
    )


def _nonnegative_integer(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _data_row_count(root: Path) -> Tuple[Optional[int], list[str], list[str]]:
    data = root / "data"
    parquet_paths = sorted(data.rglob("*.parquet")) if data.is_dir() else []
    if not parquet_paths:
        return None, [], []
    try:
        import pyarrow.parquet as parquet  # type: ignore[import-not-found,import-untyped]
    except ImportError:
        return None, [], ["pyarrow_dependency_missing"]
    total = 0
    paths: list[str] = []
    problems: list[str] = []
    for path in parquet_paths:
        relative = path.relative_to(root).as_posix()
        paths.append(relative)
        try:
            total += int(parquet.ParquetFile(path).metadata.num_rows)
        except Exception as exc:  # pragma: no cover - dependency error variants
            problems.append(f"{relative}:unreadable:{type(exc).__name__}")
    return (total if not problems else None), paths, problems


def _episode_targets(
    root: Path,
) -> Tuple[
    Dict[str, int],
    Dict[str, Dict[str, Any]],
    list[Dict[str, Any]],
    set[str],
]:
    records, paths, record_problems = _metadata_records(root, "episodes")
    targets: Dict[str, int] = {}
    derivations: Dict[str, Dict[str, Any]] = {}
    unresolved: list[Dict[str, Any]] = []
    episode_tasks: set[str] = set()
    indexes: list[int] = []
    lengths: list[int] = []
    index_problems = list(record_problems)
    length_problems = list(record_problems)
    if not paths:
        return targets, derivations, unresolved, episode_tasks
    for ordinal, record in enumerate(records):
        raw_index = record.get("episode_index", record.get("index"))
        index = _nonnegative_integer(raw_index)
        if index is None:
            index_problems.append(f"episode_record:{ordinal}:invalid_index")
        else:
            indexes.append(index)
        length = _nonnegative_integer(record.get("length"))
        if length is None:
            length_problems.append(f"episode_record:{ordinal}:invalid_length")
        else:
            lengths.append(length)
        task_value = record.get("tasks", record.get("task"))
        if isinstance(task_value, str) and task_value:
            episode_tasks.add(task_value)
        elif isinstance(task_value, list):
            episode_tasks.update(
                item for item in task_value if isinstance(item, str) and item
            )
    index_is_unique = (
        not index_problems
        and len(indexes) == len(records)
        and len(set(indexes)) == len(indexes)
    )
    if index_is_unique:
        targets["total_episodes"] = len(records)
        derivations["total_episodes"] = {
            "method": "count_unique_episode_records",
            "evidence_paths": paths,
        }
    elif index_problems:
        unresolved.append(
            _derived_unresolved(
                "total_episodes",
                "episode_metadata_is_ambiguous",
                paths,
                index_problems,
            )
        )

    data_rows, data_paths, data_problems = _data_row_count(root)
    layout_is_ambiguous = any(
        problem.endswith(":mixed_jsonl_parquet_layout")
        for problem in record_problems
    )
    if data_rows is not None and not layout_is_ambiguous:
        targets["total_frames"] = data_rows
        derivations["total_frames"] = {
            "method": "count_parquet_rows",
            "evidence_paths": data_paths,
        }
    elif (
        not length_problems
        and len(lengths) == len(records)
        and index_is_unique
    ):
        targets["total_frames"] = sum(lengths)
        derivations["total_frames"] = {
            "method": "sum_validated_episode_lengths",
            "evidence_paths": paths,
        }
    elif data_problems or length_problems or index_problems:
        unresolved.append(
            _derived_unresolved(
                "total_frames",
                "frame_extent_is_ambiguous",
                sorted(set(paths + data_paths)),
                sorted(
                    set(index_problems + length_problems + data_problems)
                ),
            )
        )
    return targets, derivations, unresolved, episode_tasks


def _task_target(
    root: Path,
    episode_tasks: Iterable[str],
) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    records, paths, problems = _metadata_records(root, "tasks")
    if paths:
        indexes: list[int] = []
        names: list[str] = []
        for ordinal, record in enumerate(records):
            raw_index = record.get("task_index", record.get("index"))
            index = _nonnegative_integer(raw_index)
            name = record.get("task")
            if index is None:
                problems.append(f"task_record:{ordinal}:invalid_index")
            else:
                indexes.append(index)
            if not isinstance(name, str) or not name:
                problems.append(f"task_record:{ordinal}:invalid_task")
            else:
                names.append(name)
        mapping_is_unique = (
            not problems
            and len(indexes) == len(records)
            and len(names) == len(records)
            and len(set(indexes)) == len(indexes)
            and len(set(names)) == len(names)
        )
        missing_episode_tasks = sorted(set(episode_tasks) - set(names))
        if missing_episode_tasks:
            problems.append(
                "episode_tasks_missing_from_ledger:"
                + ",".join(missing_episode_tasks)
            )
            mapping_is_unique = False
        if mapping_is_unique:
            return (
                len(records),
                {
                    "method": "count_unique_task_mapping",
                    "evidence_paths": paths,
                },
                None,
            )
        return (
            None,
            None,
            _derived_unresolved(
                "total_tasks",
                "task_mapping_is_ambiguous",
                paths,
                problems,
            ),
        )
    unique_tasks = sorted(set(episode_tasks))
    if unique_tasks:
        return (
            len(unique_tasks),
            {
                "method": "count_unique_episode_task_names",
                "evidence_paths": ["meta/episodes"],
            },
            None,
        )
    return None, None, None


def _video_target(
    root: Path,
) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    videos = root / "videos"
    if not videos.is_dir():
        return None, None, None
    paths: list[str] = []
    problems: list[str] = []
    for candidate in sorted(videos.rglob("*")):
        if candidate.is_symlink():
            problems.append(
                f"{candidate.relative_to(root).as_posix()}:symlink_not_counted"
            )
            continue
        if candidate.is_file() and candidate.suffix.lower() in VIDEO_EXTENSIONS:
            paths.append(candidate.relative_to(root).as_posix())
    if problems:
        return (
            None,
            None,
            _derived_unresolved(
                "total_videos",
                "video_inventory_is_ambiguous",
                paths,
                problems,
            ),
        )
    return (
        len(paths),
        {
            "method": "count_local_video_files",
            "evidence_paths": paths or ["videos"],
        },
        None,
    )


def _derived_unresolved(
    field: str,
    reason_code: str,
    evidence_paths: Sequence[str],
    problems: Sequence[str],
) -> Dict[str, Any]:
    finding = {
        "code": "LEROBOT_DERIVED_TOTAL_UNVERIFIED",
        "severity": "error",
        "layer": "metadata",
        "message": f"{field} cannot be uniquely recomputed from validated evidence.",
        "path": _INFO_ARTIFACT,
        "location": {"json_pointer": f"/{field}"},
        "evidence": {
            "field": field,
            "evidence_paths": sorted(set(evidence_paths)),
            "problems": sorted(set(problems)),
        },
        "impact": "derived_metadata_cannot_be_verified",
        "fixability": "manual",
        "remediation_ref": (
            "openbot://remediation/LEROBOT_DERIVED_TOTAL_UNVERIFIED"
        ),
    }
    return _unresolved_entry(finding, reason_code=reason_code)


def _adapter_unresolved(
    adapter: str,
    dataset_format_version: Any,
) -> Dict[str, Any]:
    finding = {
        "code": "LEROBOT_REPAIR_ADAPTER_UNVERIFIED",
        "severity": "error",
        "layer": "metadata",
        "message": (
            "Automatic repair is disabled because the dataset layout is not "
            "covered by an exact repair adapter."
        ),
        "path": _INFO_ARTIFACT,
        "location": {},
        "evidence": {
            "adapter": adapter,
            "dataset_format_version": _json_safe(dataset_format_version),
            "supported_adapters": ["lerobot_v21", "lerobot_v30"],
        },
        "impact": "repair_layout_contract_is_unverified",
        "fixability": "not_repairable",
        "remediation_ref": (
            "openbot://remediation/LEROBOT_REPAIR_ADAPTER_UNVERIFIED"
        ),
    }
    return _unresolved_entry(
        finding,
        reason_code="repair_adapter_is_not_exactly_supported",
    )


def _derive_info_totals(
    root: Path,
) -> Tuple[Dict[str, int], Dict[str, Dict[str, Any]], list[Dict[str, Any]]]:
    targets, derivations, unresolved, episode_tasks = _episode_targets(root)
    task_count, task_derivation, task_unresolved = _task_target(
        root,
        episode_tasks,
    )
    if task_count is not None and task_derivation is not None:
        targets["total_tasks"] = task_count
        derivations["total_tasks"] = task_derivation
    if task_unresolved is not None:
        unresolved.append(task_unresolved)
    video_count, video_derivation, video_unresolved = _video_target(root)
    if video_count is not None and video_derivation is not None:
        targets["total_videos"] = video_count
        derivations["total_videos"] = video_derivation
    if video_unresolved is not None:
        unresolved.append(video_unresolved)
    return targets, derivations, unresolved


def _run_audit(
    root: Path,
    *,
    input_format: str,
    checksum: Optional[str],
    integrity: str,
    follow_symlinks: bool,
    prepared: Optional[DatasetSnapshot] = None,
) -> Dict[str, Any]:
    snapshot = prepared or prepare_dataset(
        str(root),
        input_format=input_format,
        checksum=checksum,
        integrity=integrity,
        follow_symlinks=follow_symlinks,
    )
    return audit_dataset(
        str(root),
        input_format=input_format,
        checksum=checksum,
        integrity=integrity,
        follow_symlinks=follow_symlinks,
        snapshot=snapshot,
    )


def _finding_identity(finding: Mapping[str, Any]) -> str:
    payload = {
        "code": finding.get("code"),
        "path": finding.get("path"),
        "location": finding.get("location", {}),
    }
    return dataset_fingerprint(payload)


def _unresolved_entry(
    finding: Mapping[str, Any],
    *,
    reason_code: str = "automatic_repair_not_permitted",
) -> Dict[str, Any]:
    normalized = dict(_json_safe(finding))
    fixability = str(normalized.get("fixability", "manual"))
    if fixability not in {"automatic", "delegated", "manual", "not_repairable"}:
        fixability = "not_repairable"
    code = str(normalized.get("code", "UNKNOWN_FINDING"))
    high_risk = any(
        token in code
        for token in (
            "ACTION",
            "STATE",
            "NAN",
            "NONFINITE",
            "TIMESTAMP",
            "TRIM",
            "IDLE",
            "SEGMENT",
        )
    )
    return {
        "finding": normalized,
        "fixability": fixability,
        "reason_code": reason_code,
        "risk": "high" if high_risk else "medium",
        "remediation_steps": [
            {
                "kind": "manual_review",
                "instruction": (
                    "Review the evidence and use a format-aware external operation; "
                    "the conservative core executor will not mutate this payload."
                ),
                "command": None,
            }
        ],
    }


def _step(
    field: str,
    before_exists: bool,
    before: Any,
    after: int,
    derivation: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "id": f"repair.info.{field}",
        "operation": "set_derived_json_value",
        "artifact": _INFO_ARTIFACT,
        "json_pointer": f"/{field}",
        "before_exists": before_exists,
        "before": _json_safe(before),
        "after": after,
        "derivation": dict(_json_safe(derivation)),
        "fixability": "automatic",
        "risk": "low",
        "preconditions": [
            "source_snapshot_fingerprint_matches",
            "source_tree_sha256_matches",
            "derivation_remains_unique",
            "target_json_value_matches_plan_before_value",
        ],
        "finding_codes": sorted(_ADDRESSED_FINDINGS.get(field, set())),
    }


def _plan_payload_fingerprint(plan: Mapping[str, Any]) -> str:
    return dataset_fingerprint(
        {
            key: value
            for key, value in plan.items()
            if key != "plan_fingerprint"
        }
    )


def _receipt_payload_fingerprint(receipt: Mapping[str, Any]) -> str:
    return dataset_fingerprint(
        {
            key: value
            for key, value in receipt.items()
            if key != "receipt_fingerprint"
        }
    )


def _load_plan(value: PlanValue) -> Dict[str, Any]:
    if isinstance(value, (str, Path)):
        loaded = _read_json_object(Path(value), label="Repair plan")
    elif isinstance(value, Mapping):
        loaded = dict(_json_safe(value))
    else:
        raise DatasetArgumentError("Repair plan must be a mapping or JSON path")
    if set(loaded) != _PLAN_REQUIRED_KEYS:
        missing = sorted(_PLAN_REQUIRED_KEYS - set(loaded))
        extra = sorted(set(loaded) - _PLAN_REQUIRED_KEYS)
        raise DatasetArgumentError(
            f"Repair plan has an invalid top-level contract; missing={missing}, extra={extra}"
        )
    if loaded.get("schema_version") != REPAIR_PLAN_SCHEMA_VERSION:
        raise DatasetArgumentError(
            f"Repair plan schema_version must be {REPAIR_PLAN_SCHEMA_VERSION}"
        )
    if loaded.get("fingerprint_version") != REPAIR_PLAN_FINGERPRINT_VERSION:
        raise DatasetArgumentError(
            "Repair plan has an unsupported fingerprint_version"
        )
    if loaded.get("status") not in {
        "repairable",
        "no_automatic_repairs",
    }:
        raise DatasetArgumentError("Repair plan has an invalid status")
    source = loaded.get("source")
    if not isinstance(source, dict):
        raise DatasetArgumentError("Repair plan source must be an object")
    required_source_keys = {
        "snapshot_fingerprint",
        "tree_sha256",
        "expected_output_tree_sha256",
        "input_format",
        "adapter",
        "dataset_format_version",
    }
    if set(source) != required_source_keys:
        raise DatasetArgumentError("Repair plan source contract is invalid")
    for key in (
        "snapshot_fingerprint",
        "tree_sha256",
        "expected_output_tree_sha256",
    ):
        value_at_key = source.get(key)
        if (
            not isinstance(value_at_key, str)
            or len(value_at_key) != 64
            or any(character not in "0123456789abcdef" for character in value_at_key)
        ):
            raise DatasetArgumentError(
                f"Repair plan source {key} must be a SHA-256 digest"
            )
    if source.get("input_format") not in {"video", "lerobot"}:
        raise DatasetArgumentError("Repair plan source input_format is invalid")
    if not isinstance(source.get("adapter"), str) or not source["adapter"]:
        raise DatasetArgumentError("Repair plan source adapter is invalid")
    if source.get("dataset_format_version") is not None and not isinstance(
        source.get("dataset_format_version"),
        str,
    ):
        raise DatasetArgumentError(
            "Repair plan source dataset_format_version is invalid"
        )
    options = loaded.get("snapshot_options")
    if not isinstance(options, dict) or set(options) != {
        "input_format",
        "checksum",
        "integrity",
        "follow_symlinks",
    }:
        raise DatasetArgumentError(
            "Repair plan snapshot_options contract is invalid"
        )
    if options.get("input_format") != source.get("input_format"):
        raise DatasetArgumentError(
            "Repair plan snapshot input format does not match its source"
        )
    if options.get("checksum") not in {None, "sha256"}:
        raise DatasetArgumentError("Repair plan checksum option is invalid")
    if options.get("integrity") not in {"metadata", "sample", "full"}:
        raise DatasetArgumentError("Repair plan integrity option is invalid")
    if not isinstance(options.get("follow_symlinks"), bool):
        raise DatasetArgumentError(
            "Repair plan follow_symlinks option must be boolean"
        )
    source_snapshot = loaded.get("source_snapshot")
    if (
        not isinstance(source_snapshot, dict)
        or source_snapshot.get("snapshot_fingerprint")
        != source.get("snapshot_fingerprint")
    ):
        raise DatasetArgumentError(
            "Repair plan embedded source snapshot does not match its source"
        )
    snapshot_format = source_snapshot.get("format")
    if (
        not isinstance(snapshot_format, dict)
        or snapshot_format.get("input_format") != source.get("input_format")
        or snapshot_format.get("adapter") != source.get("adapter")
        or snapshot_format.get("dataset_format_version")
        != source.get("dataset_format_version")
    ):
        raise DatasetArgumentError(
            "Repair plan embedded format contract does not match its source"
        )
    if not isinstance(loaded.get("source_audit"), dict):
        raise DatasetArgumentError(
            "Repair plan embedded source audit must be an object"
        )
    if not isinstance(loaded.get("unresolved_findings"), list):
        raise DatasetArgumentError(
            "Repair plan unresolved_findings must be an array"
        )
    if not isinstance(loaded.get("safety"), dict):
        raise DatasetArgumentError("Repair plan safety contract is invalid")
    if not isinstance(loaded.get("tool_versions"), dict):
        raise DatasetArgumentError("Repair plan tool_versions must be an object")
    fingerprint = loaded.get("plan_fingerprint")
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or fingerprint != _plan_payload_fingerprint(loaded)
    ):
        raise DatasetArgumentError("Repair plan fingerprint does not match its content")
    steps = _validate_steps(loaded)
    if (loaded["status"] == "repairable") != bool(steps):
        raise DatasetArgumentError(
            "Repair plan status does not match its automatic steps"
        )
    if not steps and source["expected_output_tree_sha256"] != source["tree_sha256"]:
        raise DatasetArgumentError(
            "No-op repair plan must preserve the source tree hash"
        )
    return loaded


def _validate_steps(plan: Mapping[str, Any]) -> list[Dict[str, Any]]:
    raw_steps = plan.get("steps")
    if not isinstance(raw_steps, list):
        raise DatasetArgumentError("Repair plan steps must be an array")
    steps: list[Dict[str, Any]] = []
    ids: set[str] = set()
    pointers: set[str] = set()
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            raise DatasetArgumentError("Every repair step must be an object")
        step = dict(raw_step)
        if set(step) != _STEP_REQUIRED_KEYS:
            raise DatasetArgumentError(
                "Repair step has an invalid executable contract"
            )
        step_id = step.get("id")
        pointer = step.get("json_pointer")
        after = step.get("after")
        if not isinstance(step_id, str) or not step_id:
            raise DatasetArgumentError("Repair step id must be a non-empty string")
        if step_id in ids:
            raise DatasetArgumentError(f"Duplicate repair step id: {step_id}")
        ids.add(step_id)
        if (
            step.get("operation") != "set_derived_json_value"
            or step.get("artifact") != _INFO_ARTIFACT
            or step.get("fixability") != "automatic"
            or step.get("risk") != "low"
            or pointer not in _ALLOWED_INFO_POINTERS
        ):
            raise DatasetArgumentError(
                f"Repair step {step_id} is ambiguous or outside the P0 allowlist"
            )
        if pointer in pointers:
            raise DatasetArgumentError(f"Duplicate repair target: {pointer}")
        pointers.add(str(pointer))
        field = _ALLOWED_INFO_POINTERS[str(pointer)]
        if step_id != f"repair.info.{field}":
            raise DatasetArgumentError(
                f"Repair step {step_id} does not match its target"
            )
        if (
            isinstance(after, bool)
            or not isinstance(after, int)
            or after < 0
        ):
            raise DatasetArgumentError(
                f"Repair step {step_id} has a non-derived integer target"
            )
        if not isinstance(step.get("before_exists"), bool):
            raise DatasetArgumentError(
                f"Repair step {step_id} has no exact before-value precondition"
            )
        derivation = step.get("derivation")
        if (
            not isinstance(derivation, dict)
            or set(derivation) != {"method", "evidence_paths"}
            or derivation.get("method") not in _DERIVATION_METHODS
            or not isinstance(derivation.get("evidence_paths"), list)
            or any(
                not isinstance(path, str) or not path
                for path in derivation.get("evidence_paths", [])
            )
        ):
            raise DatasetArgumentError(
                f"Repair step {step_id} has no derivation evidence"
            )
        preconditions = step.get("preconditions")
        finding_codes = step.get("finding_codes")
        if (
            not isinstance(preconditions, list)
            or not preconditions
            or any(
                not isinstance(item, str) or not item
                for item in preconditions
            )
            or not isinstance(finding_codes, list)
            or any(
                not isinstance(item, str) or not item
                for item in finding_codes
            )
        ):
            raise DatasetArgumentError(
                f"Repair step {step_id} has invalid preconditions or findings"
            )
        steps.append(step)
    return sorted(
        steps,
        key=lambda item: (
            str(item["artifact"]),
            str(item["json_pointer"]),
            str(item["id"]),
        ),
    )


def _planned_info(
    source_info: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    result = dict(source_info)
    for step in steps:
        field = _ALLOWED_INFO_POINTERS[str(step["json_pointer"])]
        result[field] = step["after"]
    _canonical_json_bytes(result)
    return result


def _validate_output_location(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        raise DatasetArgumentError("Repair output must be distinct from the source")
    if _is_within(destination, source):
        raise DatasetArgumentError(
            "Repair output cannot be placed inside the source dataset"
        )
    if _path_exists(destination):
        raise DatasetArgumentError(f"Repair output already exists: {destination}")


def _validate_artifact_output(root: Path, output_path: Optional[str]) -> None:
    if output_path is None:
        return
    destination = Path(output_path).resolve()
    if _is_within(destination, root):
        raise DatasetArgumentError(
            "Artifact output cannot be written inside the dataset being inspected"
        )


def plan_dataset_repair(
    path: str,
    *,
    input_format: str = "auto",
    checksum: Optional[str] = "sha256",
    integrity: str = "metadata",
    follow_symlinks: bool = False,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a deterministic, read-only plan for uniquely derived metadata."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise DatasetNotFoundError(f"Directory not found: {path}")
    _validate_artifact_output(root, output_path)
    source_tree_before = _tree_sha256(root)
    prepared = prepare_dataset(
        str(root),
        input_format=input_format,
        checksum=checksum,
        integrity=integrity,
        follow_symlinks=follow_symlinks,
    )
    source_snapshot = build_dataset_snapshot(
        str(root),
        input_format=input_format,
        checksum=checksum,
        integrity=integrity,
        follow_symlinks=follow_symlinks,
        snapshot=prepared,
    )
    resolved_format = str(source_snapshot["format"]["input_format"])
    source_audit = _run_audit(
        root,
        input_format=resolved_format,
        checksum=checksum,
        integrity=integrity,
        follow_symlinks=follow_symlinks,
        prepared=prepared,
    )
    targets, derivations, derived_unresolved = _derive_info_totals(root)
    adapter = str(source_snapshot["format"]["adapter"])
    if adapter not in {"lerobot_v21", "lerobot_v30"}:
        targets = {}
        derivations = {}
        derived_unresolved.append(
            _adapter_unresolved(
                adapter,
                source_snapshot["format"]["dataset_format_version"],
            )
        )

    info_path = root / _INFO_ARTIFACT
    info: Optional[Dict[str, Any]]
    try:
        info = _read_json_object(info_path, label="LeRobot info metadata")
    except DatasetArgumentError:
        info = None
        derived_unresolved.append(
            _derived_unresolved(
                "info",
                "info_metadata_is_not_a_json_object",
                [_INFO_ARTIFACT],
                ["info_json_unreadable_or_nonfinite"],
            )
        )

    steps: list[Dict[str, Any]] = []
    if info is not None:
        for field in sorted(targets):
            before_exists = field in info
            before = info.get(field)
            after = targets[field]
            if before_exists and type(before) is int and before == after:
                continue
            steps.append(
                _step(
                    field,
                    before_exists,
                    before,
                    after,
                    derivations[field],
                )
            )
        try:
            expected_info = _planned_info(info, steps)
        except DatasetArgumentError:
            steps = []
            expected_info = info
            derived_unresolved.append(
                _derived_unresolved(
                    "info",
                    "info_metadata_contains_nonfinite_or_non_json_values",
                    [_INFO_ARTIFACT],
                    ["canonical_json_serialization_failed"],
                )
            )
    else:
        expected_info = None

    addressed_codes = {
        code
        for step in steps
        for code in step.get("finding_codes", [])
        if isinstance(code, str)
    }
    unresolved = [
        _unresolved_entry(finding)
        for finding in source_audit.get("findings", [])
        if isinstance(finding, dict)
        and str(finding.get("code")) not in addressed_codes
    ]
    unresolved.extend(derived_unresolved)
    unresolved = sorted(
        unresolved,
        key=lambda item: (
            _finding_identity(item["finding"]),
            str(item["reason_code"]),
        ),
    )
    steps = sorted(
        steps,
        key=lambda item: (
            str(item["artifact"]),
            str(item["json_pointer"]),
            str(item["id"]),
        ),
    )
    overlays = (
        {_INFO_ARTIFACT: _canonical_json_bytes(expected_info)}
        if expected_info is not None and steps
        else None
    )
    expected_output_tree = _tree_sha256(root, overlays=overlays)
    source_tree_after = _tree_sha256(root)
    if source_tree_before != source_tree_after:
        raise DatasetArgumentError("Source changed while the repair plan was built")

    snapshot_options = {
        "input_format": resolved_format,
        "checksum": checksum,
        "integrity": integrity,
        "follow_symlinks": follow_symlinks,
    }
    compatibility_target = source_snapshot["format"].get("compatibility_target")
    result: Dict[str, Any] = {
        "schema_version": REPAIR_PLAN_SCHEMA_VERSION,
        "fingerprint_version": REPAIR_PLAN_FINGERPRINT_VERSION,
        "status": "repairable" if steps else "no_automatic_repairs",
        "source": {
            "snapshot_fingerprint": source_snapshot["snapshot_fingerprint"],
            "tree_sha256": source_tree_before,
            "expected_output_tree_sha256": expected_output_tree,
            "input_format": resolved_format,
            "adapter": adapter,
            "dataset_format_version": source_snapshot["format"][
                "dataset_format_version"
            ],
        },
        "snapshot_options": snapshot_options,
        "source_snapshot": source_snapshot,
        "source_audit": source_audit,
        "steps": steps,
        "unresolved_findings": unresolved,
        "safety": {
            "planning_read_only": True,
            "copy_on_write_required": True,
            "source_fingerprint_required": True,
            "atomic_destination_visibility": True,
            "unknown_fields_preserved": True,
            "payload_edits_forbidden": True,
        },
        "tool_versions": {
            "openbot_data": __version__,
            "dataset_adapter": adapter,
            "official_loader_target": compatibility_target,
        },
    }
    result["plan_fingerprint"] = _plan_payload_fingerprint(result)
    if output_path is not None:
        write_json_atomic(Path(output_path), result)
    return result


def _validate_source_against_plan(
    root: Path,
    plan: Mapping[str, Any],
) -> Tuple[Dict[str, Any], str, Dict[str, Any], list[Dict[str, Any]]]:
    options = plan["snapshot_options"]
    if not isinstance(options, dict):
        raise DatasetArgumentError("Repair plan snapshot_options must be an object")
    current_snapshot = build_dataset_snapshot(
        str(root),
        input_format=str(options["input_format"]),
        checksum=options.get("checksum"),
        integrity=str(options["integrity"]),
        follow_symlinks=bool(options["follow_symlinks"]),
    )
    source = plan["source"]
    if not isinstance(source, dict):
        raise DatasetArgumentError("Repair plan source must be an object")
    if source.get("adapter") not in {"lerobot_v21", "lerobot_v30"}:
        raise DatasetArgumentError(
            "Repair plan does not use an exact supported repair adapter"
        )
    if (
        current_snapshot["snapshot_fingerprint"]
        != source.get("snapshot_fingerprint")
    ):
        raise DatasetArgumentError(
            "Source snapshot fingerprint is stale; build a new repair plan"
        )
    tree_hash = _tree_sha256(root)
    if tree_hash != source.get("tree_sha256"):
        raise DatasetArgumentError(
            "Source tree hash is stale; build a new repair plan"
        )
    info = _read_json_object(
        root / _INFO_ARTIFACT,
        label="LeRobot info metadata",
    )
    targets, derivations, _unresolved = _derive_info_totals(root)
    steps = _validate_steps(plan)
    for step in steps:
        field = _ALLOWED_INFO_POINTERS[str(step["json_pointer"])]
        exists = field in info
        if exists != step["before_exists"] or (
            exists
            and not _same_json_value(info.get(field), step.get("before"))
        ):
            raise DatasetArgumentError(
                f"Repair step {step['id']} before-value precondition is stale"
            )
        if targets.get(field) != step["after"]:
            raise DatasetArgumentError(
                f"Repair step {step['id']} no longer has a unique derivation"
            )
        if step["derivation"] != derivations.get(field):
            raise DatasetArgumentError(
                f"Repair step {step['id']} derivation evidence is stale"
            )
        if step["finding_codes"] != sorted(
            _ADDRESSED_FINDINGS.get(field, set())
        ):
            raise DatasetArgumentError(
                f"Repair step {step['id']} finding linkage is invalid"
            )
    expected_info = _planned_info(info, steps)
    expected_tree = _tree_sha256(
        root,
        overlays={_INFO_ARTIFACT: _canonical_json_bytes(expected_info)},
    )
    if expected_tree != source.get("expected_output_tree_sha256"):
        raise DatasetArgumentError(
            "Repair plan expected output hash does not match its derived steps"
        )
    return current_snapshot, tree_hash, expected_info, steps


def _apply_step(root: Path, step: Mapping[str, Any]) -> Dict[str, Any]:
    """Apply one already validated allowlisted step inside a staging tree."""
    info_path = root / _INFO_ARTIFACT
    info = _read_json_object(info_path, label="LeRobot info metadata")
    field = _ALLOWED_INFO_POINTERS[str(step["json_pointer"])]
    exists = field in info
    if exists != step["before_exists"] or (
        exists
        and not _same_json_value(info.get(field), step.get("before"))
    ):
        raise DatasetArgumentError(
            f"Repair step {step['id']} staging precondition did not match"
        )
    info[field] = step["after"]
    _canonical_json_bytes(info)
    write_json_atomic(info_path, info)
    return {
        "id": step["id"],
        "operation": step["operation"],
        "artifact": step["artifact"],
        "json_pointer": step["json_pointer"],
        "before": step.get("before"),
        "after": step["after"],
        "status": "executed",
    }


def _steps_match(
    root: Path,
    steps: Sequence[Mapping[str, Any]],
) -> bool:
    try:
        info = _read_json_object(
            root / _INFO_ARTIFACT,
            label="LeRobot info metadata",
        )
    except DatasetArgumentError:
        return False
    for step in steps:
        field = _ALLOWED_INFO_POINTERS[str(step["json_pointer"])]
        if (
            field not in info
            or type(info[field]) is not int
            or info[field] != step["after"]
        ):
            return False
    return True


def _executed_steps(
    steps: Sequence[Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    return [
        {
            "id": step["id"],
            "operation": step["operation"],
            "artifact": step["artifact"],
            "json_pointer": step["json_pointer"],
            "before": step.get("before"),
            "after": step["after"],
            "status": "executed",
        }
        for step in steps
    ]


def _loader_result(
    root: Path,
    loader_runner: Optional[LoaderRunner],
) -> Dict[str, Any]:
    if loader_runner is None:
        return {
            "status": "unavailable",
            "runner": None,
            "details": {"reason_code": "loader_runner_not_provided"},
        }
    try:
        raw_result = loader_runner(str(root))
    except Exception as exc:
        return {
            "status": "failed",
            "runner": "injected",
            "details": {
                "error_type": type(exc).__name__,
                "error": str(exc).replace(str(root), "."),
            },
        }
    if isinstance(raw_result, bool):
        passed = raw_result
        details: Dict[str, Any] = {"result": raw_result}
    elif raw_result is None:
        passed = True
        details = {"result": "completed_without_result"}
    elif isinstance(raw_result, Mapping):
        normalized = dict(_json_safe(raw_result, source_root=root))
        raw_status = str(normalized.get("status", "")).lower()
        if raw_status == "unavailable":
            return {
                "status": "unavailable",
                "runner": "official_lerobot",
                "details": normalized,
            }
        if isinstance(normalized.get("success"), bool):
            passed = bool(normalized["success"])
        elif isinstance(normalized.get("ok"), bool):
            passed = bool(normalized["ok"])
        else:
            passed = raw_status in {"ok", "passed", "success", "verified"}
        details = normalized
    else:
        passed = False
        details = {
            "reason_code": "unsupported_loader_result",
            "result_type": type(raw_result).__name__,
        }
    return {
        "status": "passed" if passed else "failed",
        "runner": (
            "official_lerobot"
            if isinstance(details, Mapping)
            and str(details.get("package", "")).startswith("lerobot==")
            else "injected"
        ),
        "details": details,
    }


def _receipt_findings(
    audit_before: Mapping[str, Any],
    audit_after: Mapping[str, Any],
) -> Tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    before = [
        dict(item)
        for item in audit_before.get("findings", [])
        if isinstance(item, dict)
    ]
    after = [
        dict(item)
        for item in audit_after.get("findings", [])
        if isinstance(item, dict)
    ]
    after_by_identity = {_finding_identity(item): item for item in after}
    before_identities = {_finding_identity(item) for item in before}
    resolved = [
        item for item in before if _finding_identity(item) not in after_by_identity
    ]
    unresolved = [
        item
        for item in after
        if _finding_identity(item) in before_identities
        or _finding_identity(item) not in before_identities
    ]
    return (
        sorted(resolved, key=_finding_identity),
        sorted(unresolved, key=_finding_identity),
    )


def _build_receipt(
    plan: Mapping[str, Any],
    *,
    after_snapshot: Mapping[str, Any],
    audit_after: Mapping[str, Any],
    semantic_diff: Optional[Mapping[str, Any]] = None,
    after_tree_sha256: str,
    planned_values_match: bool,
    loader_verification: Mapping[str, Any],
    source_after_apply_tree_sha256: Optional[str],
) -> Dict[str, Any]:
    before_snapshot = plan["source_snapshot"]
    audit_before = plan["source_audit"]
    calculated_diff = (
        dict(semantic_diff)
        if semantic_diff is not None
        else diff_dataset_snapshots(before_snapshot, after_snapshot)
    )
    resolved, unresolved = _receipt_findings(audit_before, audit_after)
    source = plan["source"]
    expected_hash_match = (
        after_tree_sha256 == source["expected_output_tree_sha256"]
    )
    source_unchanged = (
        None
        if source_after_apply_tree_sha256 is None
        else source_after_apply_tree_sha256 == source["tree_sha256"]
    )
    audit_error_free = not any(
        finding.get("severity") == "error"
        for finding in audit_after.get("findings", [])
        if isinstance(finding, dict)
    )
    loader_status = loader_verification["status"]
    structural_passed = (
        expected_hash_match
        and planned_values_match
        and source_unchanged is not False
    )
    if not structural_passed or loader_status == "failed":
        status = "failed"
    elif loader_status == "passed" and audit_error_free:
        status = "verified"
    else:
        status = "unverified"
    receipt: Dict[str, Any] = {
        "schema_version": REPAIR_RECEIPT_SCHEMA_VERSION,
        "fingerprint_version": REPAIR_RECEIPT_FINGERPRINT_VERSION,
        "plan_fingerprint": plan["plan_fingerprint"],
        "status": status,
        "verified": status == "verified",
        "hashes": {
            "before_tree_sha256": source["tree_sha256"],
            "after_tree_sha256": after_tree_sha256,
            "expected_after_tree_sha256": source[
                "expected_output_tree_sha256"
            ],
            "source_after_apply_tree_sha256": source_after_apply_tree_sha256,
        },
        "before_snapshot": before_snapshot,
        "after_snapshot": dict(after_snapshot),
        "diff": calculated_diff,
        "audit_before": audit_before,
        "audit_after": dict(audit_after),
        "executed_steps": _executed_steps(_validate_steps(plan)),
        "resolved_findings": resolved,
        "unresolved_findings": unresolved,
        "loader_verification": dict(loader_verification),
        "verification_checks": {
            "expected_output_hash": expected_hash_match,
            "planned_values": planned_values_match,
            "source_unchanged": source_unchanged,
            "audit_error_free": audit_error_free,
            "loader_passed": loader_status == "passed",
        },
        "tool_versions": dict(plan["tool_versions"]),
    }
    receipt["receipt_fingerprint"] = _receipt_payload_fingerprint(receipt)
    return receipt


def apply_dataset_repair(
    path: str,
    plan: PlanValue,
    *,
    output_path: str,
    loader_runner: Optional[LoaderRunner] = None,
) -> Dict[str, Any]:
    """Apply allowlisted repair steps to a staged copy and atomically reveal it."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise DatasetNotFoundError(f"Directory not found: {path}")
    loaded_plan = _load_plan(plan)
    steps = _validate_steps(loaded_plan)
    if not steps:
        raise DatasetArgumentError(
            "Repair plan contains no unambiguous automatic steps to execute"
        )
    destination = Path(output_path).resolve()
    _validate_output_location(root, destination)
    _current_snapshot, source_tree, _expected_info, steps = (
        _validate_source_against_plan(root, loaded_plan)
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.repair-",
            dir=str(destination.parent),
        )
    )
    staged_dataset = staging_parent / "dataset"
    renamed = False
    try:
        shutil.copytree(root, staged_dataset, symlinks=True)
        if _tree_sha256(staged_dataset) != source_tree:
            raise DatasetArgumentError(
                "Staged source copy does not match the repair plan"
            )
        for step in steps:
            _apply_step(staged_dataset, step)
        staged_hash = _tree_sha256(staged_dataset)
        expected_hash = loaded_plan["source"]["expected_output_tree_sha256"]
        if staged_hash != expected_hash:
            raise DatasetArgumentError(
                "Staged repair output does not match the planned output hash"
            )
        if not _steps_match(staged_dataset, steps):
            raise DatasetArgumentError(
                "Staged repair output does not contain every planned value"
            )
        options = loaded_plan["snapshot_options"]
        after_snapshot = build_dataset_snapshot(
            str(staged_dataset),
            input_format=str(options["input_format"]),
            checksum=options.get("checksum"),
            integrity=str(options["integrity"]),
            follow_symlinks=bool(options["follow_symlinks"]),
        )
        audit_after = _run_audit(
            staged_dataset,
            input_format=str(options["input_format"]),
            checksum=options.get("checksum"),
            integrity=str(options["integrity"]),
            follow_symlinks=bool(options["follow_symlinks"]),
        )
        semantic_diff = diff_dataset_snapshots(
            loaded_plan["source_snapshot"],
            after_snapshot,
        )
        source_after = _tree_sha256(root)
        if source_after != source_tree:
            raise DatasetArgumentError(
                "Source changed while the repair was staged; destination was not created"
            )
        _publish_directory_no_replace(staged_dataset, destination)
        renamed = True
    finally:
        if not renamed and staged_dataset.exists():
            shutil.rmtree(staged_dataset)
        if staging_parent.exists():
            shutil.rmtree(staging_parent)

    loader_verification = _loader_result(destination, loader_runner)
    post_loader_hash = _tree_sha256(destination)
    if post_loader_hash != staged_hash:
        after_snapshot = build_dataset_snapshot(
            str(destination),
            input_format=str(options["input_format"]),
            checksum=options.get("checksum"),
            integrity=str(options["integrity"]),
            follow_symlinks=bool(options["follow_symlinks"]),
        )
        audit_after = _run_audit(
            destination,
            input_format=str(options["input_format"]),
            checksum=options.get("checksum"),
            integrity=str(options["integrity"]),
            follow_symlinks=bool(options["follow_symlinks"]),
        )
        semantic_diff = diff_dataset_snapshots(
            loaded_plan["source_snapshot"],
            after_snapshot,
        )
    return _build_receipt(
        loaded_plan,
        after_snapshot=after_snapshot,
        audit_after=audit_after,
        semantic_diff=semantic_diff,
        after_tree_sha256=post_loader_hash,
        planned_values_match=_steps_match(destination, steps),
        loader_verification=loader_verification,
        source_after_apply_tree_sha256=source_after,
    )


def verify_dataset_repair(
    path: str,
    *,
    against: PlanValue,
    loader_runner: Optional[LoaderRunner] = None,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Re-audit and verify a repaired dataset against a deterministic plan."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise DatasetNotFoundError(f"Directory not found: {path}")
    _validate_artifact_output(root, output_path)
    loaded_plan = _load_plan(against)
    steps = _validate_steps(loaded_plan)
    options = loaded_plan["snapshot_options"]
    after_snapshot = build_dataset_snapshot(
        str(root),
        input_format=str(options["input_format"]),
        checksum=options.get("checksum"),
        integrity=str(options["integrity"]),
        follow_symlinks=bool(options["follow_symlinks"]),
    )
    audit_after = _run_audit(
        root,
        input_format=str(options["input_format"]),
        checksum=options.get("checksum"),
        integrity=str(options["integrity"]),
        follow_symlinks=bool(options["follow_symlinks"]),
    )
    after_hash = _tree_sha256(root)
    planned_values_match = bool(steps) and _steps_match(root, steps)
    loader_verification = _loader_result(root, loader_runner)
    receipt = _build_receipt(
        loaded_plan,
        after_snapshot=after_snapshot,
        audit_after=audit_after,
        semantic_diff=None,
        after_tree_sha256=after_hash,
        planned_values_match=planned_values_match,
        loader_verification=loader_verification,
        source_after_apply_tree_sha256=None,
    )
    if output_path is not None:
        write_json_atomic(Path(output_path), receipt)
    return receipt
