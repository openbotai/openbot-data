"""Semantic comparison for ``openbot.dataset_snapshot.v1`` artifacts."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from openbot_data import __version__
from openbot_data.errors import DatasetArgumentError
from openbot_data.preflight import dataset_fingerprint
from openbot_data.schema import schema_path
from openbot_data.serialization import write_json_atomic
from openbot_data.snapshot import SNAPSHOT_SCHEMA_VERSION

DIFF_SCHEMA_VERSION = "openbot.dataset_diff.v1"
DIFF_FINGERPRINT_VERSION = "openbot.dataset_diff.fingerprint.v1"
CLASSIFICATION_RANK = {
    "unchanged": 0,
    "non_breaking": 1,
    "material": 2,
    "breaking": 3,
}
SNAPSHOT_COMPONENTS = (
    "source",
    "format",
    "features",
    "tasks",
    "episodes",
    "video_streams",
    "totals",
    "metadata_inventory",
    "data_inventory",
    "media_inventory",
    "coverage",
)
SNAPSHOT_FINGERPRINT_FIELDS = (
    "schema_version",
    "fingerprint_version",
    "source",
    "format",
    "contract",
    "inventory",
    "totals",
    "coverage",
    "component_fingerprints",
)


@lru_cache(maxsize=1)
def _snapshot_validator() -> Draft202012Validator:
    with schema_path("snapshot") as path:
        schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _json_pointer(parts: Iterable[object]) -> str:
    encoded = [
        str(part).replace("~", "~0").replace("/", "~1")
        for part in parts
    ]
    return "/" + "/".join(encoded) if encoded else "/"


def _snapshot_components(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    contract = snapshot["contract"]
    inventory = snapshot["inventory"]
    return {
        "source": snapshot["source"],
        "format": snapshot["format"],
        "features": contract["features"],
        "tasks": contract["tasks"],
        "episodes": contract["episodes"],
        "video_streams": contract["video_streams"],
        "totals": snapshot["totals"],
        "metadata_inventory": inventory["metadata"],
        "data_inventory": inventory["data"],
        "media_inventory": inventory["media"],
        "coverage": snapshot["coverage"],
    }


def _validate_canonical_snapshot(snapshot: Dict[str, Any]) -> None:
    try:
        json.dumps(
            snapshot,
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DatasetArgumentError(
            "Snapshot must contain only finite JSON values"
        ) from exc

    errors = sorted(
        _snapshot_validator().iter_errors(snapshot),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            str(error.validator),
            error.message,
        ),
    )
    if errors:
        error = errors[0]
        raise DatasetArgumentError(
            "Snapshot does not match the openbot.dataset_snapshot.v1 JSON Schema "
            f"at {_json_pointer(error.absolute_path)} "
            f"(failed {error.validator})"
        )

    components = _snapshot_components(snapshot)
    component_fingerprints = snapshot["component_fingerprints"]
    if set(component_fingerprints) != set(SNAPSHOT_COMPONENTS):
        raise DatasetArgumentError(
            "Snapshot component_fingerprints must contain exactly the canonical "
            "component set"
        )
    for name in SNAPSHOT_COMPONENTS:
        expected = dataset_fingerprint(components[name])
        if component_fingerprints[name] != expected:
            raise DatasetArgumentError(
                f"Snapshot component fingerprint does not match {name}"
            )

    fingerprint_payload = {
        field: snapshot[field]
        for field in SNAPSHOT_FINGERPRINT_FIELDS
    }
    expected_snapshot_fingerprint = dataset_fingerprint(fingerprint_payload)
    if snapshot["snapshot_fingerprint"] != expected_snapshot_fingerprint:
        raise DatasetArgumentError(
            "Snapshot snapshot_fingerprint does not match its canonical content"
        )


def _load_snapshot(value: Mapping[str, Any] | str | Path) -> Dict[str, Any]:
    if isinstance(value, (str, Path)):
        path = Path(value)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DatasetArgumentError(f"Snapshot could not be read: {path}") from exc
    else:
        loaded = dict(value)
    if not isinstance(loaded, dict):
        raise DatasetArgumentError("Snapshot must be a JSON object")
    if loaded.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise DatasetArgumentError(
            f"Snapshot schema_version must be {SNAPSHOT_SCHEMA_VERSION}"
        )
    _validate_canonical_snapshot(loaded)
    return loaded


def _items_by_key(
    values: object,
    key: str,
) -> Dict[Any, Dict[str, Any]]:
    if not isinstance(values, list):
        return {}
    result: Dict[Any, Dict[str, Any]] = {}
    for value in values:
        if isinstance(value, dict) and key in value:
            result[value[key]] = value
    return result


def _change(
    classification: str,
    component: str,
    kind: str,
    path: str,
    *,
    before: Any = None,
    after: Any = None,
) -> Dict[str, Any]:
    return {
        "classification": classification,
        "component": component,
        "kind": kind,
        "path": path,
        "before": before,
        "after": after,
    }


def _compare_keyed(
    baseline: Dict[Any, Dict[str, Any]],
    candidate: Dict[Any, Dict[str, Any]],
    *,
    component: str,
    path_prefix: str,
    added_class: str,
    removed_class: str,
) -> list[Dict[str, Any]]:
    changes: list[Dict[str, Any]] = []
    for key in sorted(set(baseline) | set(candidate), key=lambda item: str(item)):
        path = f"{path_prefix}/{key}"
        if key not in baseline:
            changes.append(
                _change(
                    added_class,
                    component,
                    "added",
                    path,
                    after=candidate[key],
                )
            )
        elif key not in candidate:
            changes.append(
                _change(
                    removed_class,
                    component,
                    "removed",
                    path,
                    before=baseline[key],
                )
            )
    return changes


def _compare_features(
    baseline_snapshot: Dict[str, Any],
    candidate_snapshot: Dict[str, Any],
) -> list[Dict[str, Any]]:
    baseline = _items_by_key(
        baseline_snapshot.get("contract", {}).get("features", []),
        "key",
    )
    candidate = _items_by_key(
        candidate_snapshot.get("contract", {}).get("features", []),
        "key",
    )
    changes = _compare_keyed(
        baseline,
        candidate,
        component="features",
        path_prefix="contract/features",
        added_class="non_breaking",
        removed_class="breaking",
    )
    for key in sorted(set(baseline) & set(candidate), key=str):
        before = baseline[key]
        after = candidate[key]
        for field in ("dtype", "shape"):
            if before.get(field) != after.get(field):
                changes.append(
                    _change(
                        "breaking",
                        "features",
                        f"{field}_changed",
                        f"contract/features/{key}/{field}",
                        before=before.get(field),
                        after=after.get(field),
                    )
                )
        for field in ("names", "metadata"):
            if before.get(field) != after.get(field):
                changes.append(
                    _change(
                        "non_breaking",
                        "features",
                        f"{field}_changed",
                        f"contract/features/{key}/{field}",
                        before=before.get(field),
                        after=after.get(field),
                    )
                )
    return changes


def _compare_tasks(
    baseline_snapshot: Dict[str, Any],
    candidate_snapshot: Dict[str, Any],
) -> list[Dict[str, Any]]:
    baseline = _items_by_key(
        baseline_snapshot.get("contract", {}).get("tasks", []),
        "task",
    )
    candidate = _items_by_key(
        candidate_snapshot.get("contract", {}).get("tasks", []),
        "task",
    )
    changes = _compare_keyed(
        baseline,
        candidate,
        component="tasks",
        path_prefix="contract/tasks",
        added_class="material",
        removed_class="material",
    )
    remapped_tasks: set[str] = set()
    for task in sorted(set(baseline) & set(candidate), key=str):
        before_index = baseline[task].get("task_index")
        after_index = candidate[task].get("task_index")
        if before_index != after_index:
            remapped_tasks.add(str(task))
            changes.append(
                _change(
                    "breaking",
                    "tasks",
                    "task_index_remapped",
                    f"contract/tasks/{task}/task_index",
                    before=before_index,
                    after=after_index,
                )
            )

    baseline_by_index = {
        item["task_index"]: item
        for item in baseline.values()
        if item.get("task_index") is not None
    }
    candidate_by_index = {
        item["task_index"]: item
        for item in candidate.values()
        if item.get("task_index") is not None
    }
    for task_index in sorted(set(baseline_by_index) & set(candidate_by_index)):
        before_task = baseline_by_index[task_index]["task"]
        after_task = candidate_by_index[task_index]["task"]
        if (
            before_task != after_task
            and str(before_task) not in remapped_tasks
            and str(after_task) not in remapped_tasks
        ):
            changes.append(
                _change(
                    "breaking",
                    "tasks",
                    "task_identity_remapped",
                    f"contract/tasks/by-index/{task_index}",
                    before=before_task,
                    after=after_task,
                )
            )
    return changes


def _compare_episodes(
    baseline_snapshot: Dict[str, Any],
    candidate_snapshot: Dict[str, Any],
) -> list[Dict[str, Any]]:
    baseline = _items_by_key(
        baseline_snapshot.get("contract", {}).get("episodes", []),
        "episode_index",
    )
    candidate = _items_by_key(
        candidate_snapshot.get("contract", {}).get("episodes", []),
        "episode_index",
    )
    changes = _compare_keyed(
        baseline,
        candidate,
        component="episodes",
        path_prefix="contract/episodes",
        added_class="material",
        removed_class="material",
    )
    for key in sorted(set(baseline) & set(candidate)):
        before = baseline[key]
        after = candidate[key]
        if before.get("source_ordinal") != after.get("source_ordinal"):
            changes.append(
                _change(
                    "material",
                    "episodes",
                    "reordered",
                    f"contract/episodes/{key}/source_ordinal",
                    before=before.get("source_ordinal"),
                    after=after.get("source_ordinal"),
                )
            )
        content_fields = (
            "length",
            "tasks",
            "video_files",
            "video_segments",
            "data_relation",
            "content_sha256",
            "content_rows",
        )
        before_content = {
            field: before.get(field)
            for field in content_fields
        }
        after_content = {
            field: after.get(field)
            for field in content_fields
        }
        if before_content != after_content:
            changes.append(
                _change(
                    "material",
                    "episodes",
                    "content_changed",
                    f"contract/episodes/{key}",
                    before=before_content,
                    after=after_content,
                )
            )
        if before.get("source") != after.get("source"):
            changes.append(
                _change(
                    "non_breaking",
                    "episodes",
                    "source_changed",
                    f"contract/episodes/{key}/source",
                    before=before.get("source"),
                    after=after.get("source"),
                )
            )
        if before.get("extensions") != after.get("extensions"):
            changes.append(
                _change(
                    "non_breaking",
                    "episodes",
                    "metadata_changed",
                    f"contract/episodes/{key}/extensions",
                    before=before.get("extensions"),
                    after=after.get("extensions"),
                )
            )
    return changes


def _compare_streams(
    baseline_snapshot: Dict[str, Any],
    candidate_snapshot: Dict[str, Any],
) -> list[Dict[str, Any]]:
    baseline = _items_by_key(
        baseline_snapshot.get("contract", {}).get("video_streams", []),
        "key",
    )
    candidate = _items_by_key(
        candidate_snapshot.get("contract", {}).get("video_streams", []),
        "key",
    )
    changes = _compare_keyed(
        baseline,
        candidate,
        component="video_streams",
        path_prefix="contract/video_streams",
        added_class="non_breaking",
        removed_class="breaking",
    )
    for key in sorted(set(baseline) & set(candidate), key=str):
        if baseline[key] != candidate[key]:
            changes.append(
                _change(
                    "material",
                    "video_streams",
                    "metadata_changed",
                    f"contract/video_streams/{key}",
                    before=baseline[key],
                    after=candidate[key],
                )
            )
    return changes


def _compare_inventory(
    baseline_snapshot: Dict[str, Any],
    candidate_snapshot: Dict[str, Any],
) -> list[Dict[str, Any]]:
    changes: list[Dict[str, Any]] = []
    for group in ("metadata", "data", "media"):
        added_class = "non_breaking" if group == "metadata" else "material"
        content_class = "non_breaking" if group == "metadata" else "material"
        baseline = _items_by_key(
            baseline_snapshot.get("inventory", {}).get(group, []),
            "path",
        )
        candidate = _items_by_key(
            candidate_snapshot.get("inventory", {}).get(group, []),
            "path",
        )
        changes.extend(
            _compare_keyed(
                baseline,
                candidate,
                component=f"{group}_inventory",
                path_prefix=f"inventory/{group}",
                added_class=added_class,
                removed_class="material",
            )
        )
        for path in sorted(set(baseline) & set(candidate), key=str):
            if baseline[path] != candidate[path]:
                changes.append(
                    _change(
                        content_class,
                        f"{group}_inventory",
                        "content_changed",
                        f"inventory/{group}/{path}",
                        before=baseline[path],
                        after=candidate[path],
                    )
                )
    return changes


def _compare_totals(
    baseline_snapshot: Dict[str, Any],
    candidate_snapshot: Dict[str, Any],
) -> list[Dict[str, Any]]:
    baseline = baseline_snapshot.get("totals", {})
    candidate = candidate_snapshot.get("totals", {})
    changes: list[Dict[str, Any]] = []
    for field in sorted(set(baseline) | set(candidate)):
        before = baseline.get(field)
        after = candidate.get(field)
        if before == after:
            continue
        if field in {"features", "video_streams"}:
            classification = "non_breaking" if after > before else "breaking"
        elif field == "metadata_shards":
            classification = "non_breaking" if after > before else "material"
        elif field == "size_bytes":
            baseline_inventory = baseline_snapshot.get("inventory", {})
            candidate_inventory = candidate_snapshot.get("inventory", {})
            payload_changed = any(
                baseline_inventory.get(group) != candidate_inventory.get(group)
                for group in ("data", "media")
            )
            classification = "material" if payload_changed else "non_breaking"
        else:
            classification = "material"
        changes.append(
            _change(
                classification,
                "totals",
                "increased" if after > before else "decreased",
                f"totals/{field}",
                before=before,
                after=after,
            )
        )
    return changes


def _overall(changes: Iterable[Dict[str, Any]]) -> str:
    return max(
        (str(change["classification"]) for change in changes),
        key=lambda value: CLASSIFICATION_RANK[value],
        default="unchanged",
    )


def diff_dataset_snapshots(
    baseline: Mapping[str, Any] | str | Path,
    candidate: Mapping[str, Any] | str | Path,
    *,
    output_path: str | None = None,
) -> Dict[str, Any]:
    """Classify semantic changes between two portable snapshots."""
    before = _load_snapshot(baseline)
    after = _load_snapshot(candidate)
    changes: list[Dict[str, Any]] = []

    before_source = before.get("source", {})
    after_source = after.get("source", {})
    if before_source != after_source:
        changes.append(
            _change(
                "material",
                "source",
                "revision_changed",
                "source",
                before=before_source,
                after=after_source,
            )
        )

    before_format = before.get("format", {})
    after_format = after.get("format", {})
    for field in ("input_format", "adapter", "dataset_format_version"):
        if before_format.get(field) != after_format.get(field):
            changes.append(
                _change(
                    "breaking",
                    "format",
                    f"{field}_changed",
                    f"format/{field}",
                    before=before_format.get(field),
                    after=after_format.get(field),
                )
            )
    if before_format.get("metadata") != after_format.get("metadata"):
        changes.append(
            _change(
                "non_breaking",
                "format",
                "metadata_changed",
                "format/metadata",
                before=before_format.get("metadata"),
                after=after_format.get("metadata"),
            )
        )

    changes.extend(_compare_features(before, after))
    changes.extend(_compare_tasks(before, after))
    changes.extend(_compare_episodes(before, after))
    changes.extend(_compare_streams(before, after))
    changes.extend(_compare_inventory(before, after))
    changes.extend(_compare_totals(before, after))
    if before.get("coverage") != after.get("coverage"):
        changes.append(
            _change(
                "non_breaking",
                "coverage",
                "coverage_changed",
                "coverage",
                before=before.get("coverage"),
                after=after.get("coverage"),
            )
        )

    changes = sorted(
        changes,
        key=lambda item: (
            -CLASSIFICATION_RANK[str(item["classification"])],
            str(item["component"]),
            str(item["path"]),
            str(item["kind"]),
        ),
    )
    classification = _overall(changes)
    component_fingerprints_before = before.get("component_fingerprints", {})
    component_fingerprints_after = after.get("component_fingerprints", {})
    component_status = {
        key: (
            "unchanged"
            if component_fingerprints_before.get(key)
            == component_fingerprints_after.get(key)
            else "changed"
        )
        for key in sorted(
            set(component_fingerprints_before) | set(component_fingerprints_after)
        )
    }
    counts = {
        name: sum(change["classification"] == name for change in changes)
        for name in ("breaking", "material", "non_breaking")
    }
    fingerprint_payload = {
        "schema_version": DIFF_SCHEMA_VERSION,
        "fingerprint_version": DIFF_FINGERPRINT_VERSION,
        "baseline_fingerprint": before["snapshot_fingerprint"],
        "candidate_fingerprint": after["snapshot_fingerprint"],
        "classification": classification,
        "summary": {"changes": len(changes), **counts},
        "component_status": component_status,
        "changes": changes,
    }
    result = {
        **fingerprint_payload,
        "tool": {"name": "openbot-data", "version": __version__},
        "diff_fingerprint": dataset_fingerprint(fingerprint_payload),
    }
    if output_path is not None:
        write_json_atomic(Path(output_path), result)
    return result
