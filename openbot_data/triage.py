"""Deterministic finding triage and low-cost advisory evidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Optional, Sequence

_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2, "advisory": 3}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _location(finding: Mapping[str, Any]) -> dict[str, Any]:
    raw = finding.get("location")
    location = dict(raw) if isinstance(raw, Mapping) else {}
    evidence = finding.get("evidence")
    if isinstance(evidence, Mapping):
        for key in (
            "episode_index",
            "frame_index",
            "frame_from",
            "frame_to",
            "feature_key",
            "video_key",
            "camera",
            "shard",
        ):
            if key not in location and key in evidence:
                location[key] = evidence[key]
    return location


def _group_identity(
    finding: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    location = _location(finding)
    if "episode_index" in location:
        value = {"episode_index": location["episode_index"]}
        if "frame_index" in location:
            value["frame_index"] = location["frame_index"]
        elif "frame_from" in location or "frame_to" in location:
            value["frame_from"] = location.get("frame_from")
            value["frame_to"] = location.get("frame_to")
        return "episode", _canonical(value), value
    for key, kind in (
        ("feature_key", "feature"),
        ("video_key", "camera"),
        ("camera", "camera"),
        ("shard", "shard"),
    ):
        if key in location:
            value = {key: location[key]}
            return kind, _canonical(value), value
    raw_path = finding.get("path")
    if isinstance(raw_path, str) and raw_path:
        value = {"path": raw_path}
        return "shard", _canonical(value), value
    return "dataset", "{}", {}


def _finding_sort_key(finding: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _SEVERITY_RANK.get(str(finding.get("severity", "")), 99),
        str(finding.get("layer", "")),
        str(finding.get("code", "")),
        str(finding.get("path", "")),
        _canonical(_location(finding)),
        _canonical(finding.get("evidence", {})),
    )


def triage_findings(
    findings: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Group findings at the most actionable stable location.

    The result contains references to canonical findings rather than a second
    interpretation of their evidence.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in findings:
        finding = dict(raw)
        kind, identity, value = _group_identity(finding)
        key = (kind, identity)
        group = grouped.setdefault(
            key,
            {
                "group": {"kind": kind, "location": value},
                "blocking_findings": [],
                "warnings": [],
                "advisories": [],
            },
        )
        severity = str(finding.get("severity", ""))
        if severity == "error":
            bucket = "blocking_findings"
        elif severity == "warning":
            bucket = "warnings"
        else:
            bucket = "advisories"
        group[bucket].append(finding)

    result = []
    kind_rank = {
        "episode": 0,
        "feature": 1,
        "camera": 2,
        "shard": 3,
        "dataset": 4,
    }
    for key in sorted(
        grouped,
        key=lambda item: (kind_rank.get(item[0], 99), item[1]),
    ):
        group = grouped[key]
        for bucket in ("blocking_findings", "warnings", "advisories"):
            group[bucket] = sorted(group[bucket], key=_finding_sort_key)
        result.append(group)
    return result


def _signal(
    code: str,
    *,
    message: str,
    raw_value: Any,
    unit: str,
    threshold: Any,
    threshold_source: str,
    applicability: str,
    coverage: Mapping[str, Any],
    location: Mapping[str, Any],
    evidence: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "advisory",
        "layer": "data",
        "message": message,
        "raw_value": raw_value,
        "unit": unit,
        "threshold": threshold,
        "threshold_source": threshold_source,
        "applicability": applicability,
        "coverage": dict(coverage),
        "location": dict(location),
        "evidence": dict(evidence or {}),
    }


def _idle_trim_plan(
    snapshot: Mapping[str, Any],
    *,
    episode_index: object,
    feature_key: str,
    frame_from: int,
    frame_to: int,
) -> dict[str, Any]:
    """Describe the synchronized work required to trim one idle span.

    This is deliberately a non-executing review artifact. A stationary hold can
    be task-relevant, and deleting it requires rebuilding every time-aligned
    relation rather than editing only the action column.
    """
    known_artifacts: set[str] = set()
    contract = snapshot.get("contract")
    episodes = contract.get("episodes") if isinstance(contract, Mapping) else None
    if isinstance(episodes, list):
        for episode in episodes:
            if (
                not isinstance(episode, Mapping)
                or episode.get("episode_index") != episode_index
            ):
                continue
            source = episode.get("source")
            if isinstance(source, Mapping) and isinstance(source.get("path"), str):
                known_artifacts.add(str(source["path"]))
            data_relation = episode.get("data_relation")
            if isinstance(data_relation, Mapping) and isinstance(
                data_relation.get("path"), str
            ):
                known_artifacts.add(str(data_relation["path"]))
            video_segments = episode.get("video_segments")
            if isinstance(video_segments, list):
                for segment in video_segments:
                    if isinstance(segment, Mapping) and isinstance(
                        segment.get("path"), str
                    ):
                        known_artifacts.add(str(segment["path"]))
            break

    return {
        "schema_version": "openbot.idle_trim_plan.v1",
        "operation": "trim_idle_span",
        "execution_status": "review_required_not_executable",
        "review_required": True,
        "mutates_source": False,
        "scope": {
            "episode_index": episode_index,
            "feature_key": feature_key,
            "frame_from": frame_from,
            "frame_to": frame_to,
        },
        "known_artifacts": sorted(known_artifacts),
        "required_synchronization": [
            "parquet_rows",
            "timestamps",
            "frame_indices",
            "global_indices",
            "episode_relations",
            "normalization_statistics",
            "video_segments_all_cameras",
        ],
        "verification": [
            "full_post_audit",
            "official_loader_smoke",
            "semantic_snapshot_diff",
        ],
    }


def _feature_map(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    contract = snapshot.get("contract")
    features = contract.get("features") if isinstance(contract, Mapping) else None
    if not isinstance(features, list):
        return {}
    return {
        str(item["key"]): item
        for item in features
        if isinstance(item, Mapping) and isinstance(item.get("key"), str)
    }


def _episode_digest(episode: Mapping[str, Any]) -> Optional[str]:
    for key in ("content_sha256", "sha256", "data_sha256"):
        value = episode.get(key)
        if isinstance(value, str) and len(value) == 64:
            return value
    metadata = episode.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("content_sha256", "sha256", "data_sha256"):
            value = metadata.get(key)
            if isinstance(value, str) and len(value) == 64:
                return value
    return None


def _finite_values(value: object) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result = []
    for item in value:
        if isinstance(item, bool):
            return []
        try:
            number = float(item)
        except (TypeError, ValueError, OverflowError):
            return []
        if not math.isfinite(number):
            return []
        result.append(number)
    return result


def _measurement_values(value: object) -> list[float]:
    """Flatten one scalar or nested numeric measurement to finite values."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        number = float(value)
        return [number] if math.isfinite(number) else []
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return []
    result: list[float] = []
    for item in value:
        values = _measurement_values(item)
        if not values:
            return []
        result.extend(values)
    return result


def _normalized_feature_measurements(
    measurements: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Normalize legacy and validation.v1 feature evidence to one contract."""
    result: dict[str, dict[str, Any]] = {}
    legacy = measurements.get("features")
    if isinstance(legacy, Mapping):
        for key, value in legacy.items():
            if isinstance(value, Mapping):
                result[str(key)] = dict(value)

    numeric = measurements.get("numeric_features")
    global_coverage = measurements.get("coverage")
    if isinstance(numeric, Mapping):
        for key, value in numeric.items():
            if not isinstance(value, Mapping):
                continue
            normalized = result.setdefault(str(key), {})
            variances = _measurement_values(value.get("variance"))
            minima = _measurement_values(value.get("min"))
            maxima = _measurement_values(value.get("max"))
            if variances:
                normalized["variance"] = variances
            if minima:
                normalized["minimum"] = min(minima)
            if maxima:
                normalized["maximum"] = max(maxima)
            coverage = value.get("coverage", global_coverage)
            if isinstance(coverage, Mapping):
                normalized["coverage"] = dict(coverage)

    spans = measurements.get("static_action_spans")
    if isinstance(spans, list):
        for span in spans:
            if not isinstance(span, Mapping):
                continue
            feature_key = span.get("feature_key")
            if not isinstance(feature_key, str):
                continue
            start = span.get("start_frame_index")
            end = span.get("end_frame_index")
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            normalized = result.setdefault(feature_key, {})
            normalized.setdefault("idle_spans", []).append(
                {
                    "episode_index": span.get("episode_index"),
                    "frame_from": start,
                    "frame_to": end,
                }
            )
            normalized.setdefault(
                "action_semantics",
                "validated_static_action_span",
            )
            coverage = span.get("coverage", global_coverage)
            if "coverage" not in normalized and isinstance(coverage, Mapping):
                normalized["coverage"] = dict(coverage)
    return result


def _validated_digest_groups(
    measurements: Mapping[str, Any],
) -> dict[str, list[int]]:
    result: dict[str, list[int]] = defaultdict(list)
    values = measurements.get("episode_content_digests")
    if not isinstance(values, list):
        return result
    for item in values:
        if not isinstance(item, Mapping):
            continue
        digest = item.get("digest")
        algorithm = item.get("algorithm")
        episode_index = item.get("episode_index")
        if (
            algorithm == "sha256"
            and isinstance(digest, str)
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
            and isinstance(episode_index, int)
            and not isinstance(episode_index, bool)
        ):
            result[digest].append(episode_index)
    return result


def _validated_group_counts(
    measurements: Mapping[str, Any],
    key: str,
) -> Optional[Counter[str]]:
    values = measurements.get(key)
    if not isinstance(values, list):
        return None
    result: Counter[str] = Counter()
    for item in values:
        if not isinstance(item, Mapping):
            continue
        if key == "task_counts":
            identity = item.get("task")
        else:
            identity = item.get("feature_key")
        count = item.get("episode_count", item.get("row_count"))
        if (
            isinstance(identity, str)
            and identity
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count >= 0
        ):
            result[identity] = count
    return result


def analyze_advisory_signals(
    snapshot: Mapping[str, Any],
    *,
    thresholds: Optional[Mapping[str, Any]] = None,
    measurements: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Return raw, explainable signals without producing an aggregate score.

    Payload-dependent callers may pass already-discovered ``measurements``.
    This function never reads dataset files.
    """
    configured = dict(thresholds or {})
    observed = dict(measurements or {})
    contract = snapshot.get("contract")
    episodes = contract.get("episodes", []) if isinstance(contract, Mapping) else []
    streams = contract.get("video_streams", []) if isinstance(contract, Mapping) else []
    episode_items = [item for item in episodes if isinstance(item, Mapping)]
    stream_items = [item for item in streams if isinstance(item, Mapping)]
    signals: list[dict[str, Any]] = []

    fps_values = sorted(
        {
            float(fps)
            for stream in stream_items
            for fps in (stream.get("fps", []) if isinstance(stream.get("fps"), list) else [])
            if isinstance(fps, (int, float)) and not isinstance(fps, bool) and fps > 0
        }
    )
    short_threshold = configured.get("short_episode_seconds")
    if (
        isinstance(short_threshold, (int, float))
        and not isinstance(short_threshold, bool)
        and math.isfinite(float(short_threshold))
        and short_threshold >= 0
        and len(fps_values) == 1
    ):
        fps = fps_values[0]
        for episode in episode_items:
            length = episode.get("length")
            if not isinstance(length, int) or isinstance(length, bool) or length < 0:
                continue
            duration = length / fps
            if duration < float(short_threshold):
                signals.append(
                    _signal(
                        "ADVISORY_SHORT_EPISODE",
                        message="Episode duration is below the configured review threshold.",
                        raw_value=round(duration, 9),
                        unit="seconds",
                        threshold=float(short_threshold),
                        threshold_source=str(
                            configured.get(
                                "short_episode_threshold_source",
                                "caller_config",
                            )
                        ),
                        applicability="configured_threshold_and_single_dataset_fps",
                        coverage={"checked": 1, "total": len(episode_items)},
                        location={"episode_index": episode.get("episode_index")},
                        evidence={"length_frames": length, "fps": fps},
                    )
                )

    digest_groups: dict[str, list[int]] = _validated_digest_groups(observed)
    for episode in episode_items:
        digest = _episode_digest(episode)
        index = episode.get("episode_index")
        if digest is not None and isinstance(index, int) and index not in digest_groups[digest]:
            digest_groups[digest].append(index)
    for digest, indexes in sorted(digest_groups.items()):
        if len(indexes) > 1:
            signals.append(
                _signal(
                    "ADVISORY_DUPLICATE_EPISODE_CONTENT",
                    message="Multiple episodes carry the same validated content digest.",
                    raw_value=len(indexes),
                    unit="episodes",
                    threshold=1,
                    threshold_source="exact_sha256_identity",
                    applicability="episodes_with_validated_content_digest",
                    coverage={
                        "checked": sum(len(value) for value in digest_groups.values()),
                        "total": len(episode_items),
                    },
                    location={"episode_indexes": sorted(indexes)},
                    evidence={"sha256": digest},
                )
            )

    task_counts = _validated_group_counts(observed, "task_counts")
    camera_counts = _validated_group_counts(observed, "camera_counts")
    if task_counts is None:
        task_counts = Counter()
        for episode in episode_items:
            tasks = episode.get("tasks")
            if isinstance(tasks, list):
                task_counts.update(str(task) for task in set(tasks))
    if camera_counts is None:
        camera_counts = Counter()
        for episode in episode_items:
            segments = episode.get("video_segments")
            if isinstance(segments, list):
                camera_counts.update(
                    str(segment["video_key"])
                    for segment in segments
                    if isinstance(segment, Mapping) and isinstance(segment.get("video_key"), str)
                )
    for code, counts, unit in (
        ("ADVISORY_TASK_COVERAGE_IMBALANCE", task_counts, "episodes_per_task"),
        ("ADVISORY_CAMERA_COVERAGE_IMBALANCE", camera_counts, "episodes_per_camera"),
    ):
        if len(counts) > 1 and min(counts.values()) != max(counts.values()):
            signals.append(
                _signal(
                    code,
                    message="Coverage differs across declared dataset groups.",
                    raw_value=dict(sorted(counts.items())),
                    unit=unit,
                    threshold={"expected": "balanced"},
                    threshold_source="coverage_inventory",
                    applicability="two_or_more_observed_groups",
                    coverage={
                        "checked": len(episode_items),
                        "total": len(episode_items),
                    },
                    location={},
                )
            )

    feature_measurements = _normalized_feature_measurements(observed)
    if feature_measurements:
        features = _feature_map(snapshot)
        for feature_key in sorted(feature_measurements):
            measurement = feature_measurements[feature_key]
            if not isinstance(measurement, Mapping):
                continue
            variances = _finite_values(measurement.get("variance"))
            variance_threshold = configured.get("near_zero_variance")
            if (
                variances
                and isinstance(variance_threshold, (int, float))
                and not isinstance(variance_threshold, bool)
                and float(variance_threshold) >= 0
            ):
                constant_dimensions = [
                    index
                    for index, value in enumerate(variances)
                    if value <= float(variance_threshold)
                ]
                if constant_dimensions:
                    signals.append(
                        _signal(
                            "ADVISORY_NEAR_ZERO_VARIANCE_DIMENSION",
                            message=(
                                "One or more measured feature dimensions have near-zero variance."
                            ),
                            raw_value=variances,
                            unit="variance",
                            threshold=float(variance_threshold),
                            threshold_source=str(
                                configured.get(
                                    "variance_threshold_source",
                                    "caller_config",
                                )
                            ),
                            applicability="finite_full_or_declared_sample_measurements",
                            coverage=dict(
                                measurement.get("coverage", {})
                                if isinstance(measurement.get("coverage"), Mapping)
                                else {}
                            ),
                            location={
                                "feature_key": str(feature_key),
                                "dimensions": constant_dimensions,
                            },
                        )
                    )

            spans = measurement.get("idle_spans")
            idle_threshold = configured.get("idle_action_span_frames")
            if (
                "action" in str(feature_key).lower()
                and isinstance(spans, list)
                and isinstance(idle_threshold, int)
                and not isinstance(idle_threshold, bool)
                and idle_threshold >= 0
            ):
                for span in spans:
                    if not isinstance(span, Mapping):
                        continue
                    start = span.get("frame_from")
                    end = span.get("frame_to")
                    if (
                        isinstance(start, int)
                        and isinstance(end, int)
                        and end >= start
                        and end - start + 1 >= idle_threshold
                    ):
                        signals.append(
                            _signal(
                                "ADVISORY_IDLE_ACTION_SPAN",
                                message=(
                                    "A measured action span meets the configured idle threshold."
                                ),
                                raw_value=end - start + 1,
                                unit="frames",
                                threshold=idle_threshold,
                                threshold_source=str(
                                    configured.get(
                                        "idle_threshold_source",
                                        "caller_config",
                                    )
                                ),
                                applicability=str(
                                    measurement.get(
                                        "action_semantics",
                                        "declared_action_feature",
                                    )
                                ),
                                coverage=dict(
                                    measurement.get("coverage", {})
                                    if isinstance(
                                        measurement.get("coverage"),
                                        Mapping,
                                    )
                                    else {}
                                ),
                                location={
                                    "episode_index": span.get("episode_index"),
                                    "feature_key": str(feature_key),
                                    "frame_from": start,
                                    "frame_to": end,
                                },
                                evidence={
                                    "span_bounds": [start, end],
                                    "action_semantics": measurement.get("action_semantics"),
                                    "trim_plan": _idle_trim_plan(
                                        snapshot,
                                        episode_index=span.get("episode_index"),
                                        feature_key=str(feature_key),
                                        frame_from=start,
                                        frame_to=end,
                                    ),
                                },
                            )
                        )

            declared_range = configured.get("action_range")
            minimum = measurement.get("minimum")
            maximum = measurement.get("maximum")
            if (
                "action" in str(feature_key).lower()
                and isinstance(declared_range, Sequence)
                and not isinstance(declared_range, (str, bytes, bytearray))
                and len(declared_range) == 2
                and isinstance(minimum, (int, float))
                and isinstance(maximum, (int, float))
            ):
                low = float(declared_range[0])
                high = float(declared_range[1])
                if float(minimum) <= low or float(maximum) >= high:
                    signals.append(
                        _signal(
                            "ADVISORY_ACTION_SATURATION",
                            message=(
                                "Observed action extrema meet a declared semantic range boundary."
                            ),
                            raw_value={
                                "minimum": float(minimum),
                                "maximum": float(maximum),
                            },
                            unit="declared_action_unit",
                            threshold={"minimum": low, "maximum": high},
                            threshold_source=str(
                                configured.get(
                                    "action_range_source",
                                    "policy_contract",
                                )
                            ),
                            applicability="explicit_action_range_contract",
                            coverage=dict(
                                measurement.get("coverage", {})
                                if isinstance(measurement.get("coverage"), Mapping)
                                else {}
                            ),
                            location={"feature_key": str(feature_key)},
                            evidence={"feature_contract": dict(features.get(str(feature_key), {}))},
                        )
                    )

    # Ensure accidental caller order and mapping hash order cannot alter output.
    unique: dict[str, dict[str, Any]] = {}
    for signal in signals:
        identity = hashlib.sha256(_canonical(signal).encode("utf-8")).hexdigest()
        unique[identity] = signal
    return sorted(
        unique.values(),
        key=lambda item: (
            str(item["code"]),
            _canonical(item["location"]),
            _canonical(item["raw_value"]),
        ),
    )


__all__ = ["analyze_advisory_signals", "triage_findings"]
