from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest
from typer.testing import CliRunner

import openbot_data.cli as cli_module
from openbot_data.cli import app
from openbot_data.errors import DatasetArgumentError
from openbot_data.hub import HubSourceError

ResultFunction = Callable[..., dict[str, Any]]


def _write_result(path: str | Path, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _result_stub(
    payload: dict[str, Any],
    *,
    write_output: bool = True,
    captured: list[dict[str, Any]] | None = None,
) -> ResultFunction:
    def stub(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        if captured is not None:
            captured.append(kwargs)
        if write_output and kwargs.get("output_path") is not None:
            _write_result(kwargs["output_path"], payload)
        return payload

    return stub


def _raising_stub(error: Exception) -> ResultFunction:
    def stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise error

    return stub


def test_v003_cli_canonical_positive_results_exit_zero_and_write_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payloads = {
        "snapshot": {"snapshot_fingerprint": "snapshot-positive"},
        "diff": {"classification": "unchanged"},
        "readiness": {
            "profile": {"id": "lerobot-core"},
            "status": "READY",
        },
        "repair_plan": {
            "status": "no_automatic_repairs",
            "steps": [],
            "plan_fingerprint": "plan-positive",
        },
        "repair_apply": {"status": "verified"},
        "repair_verify": {"status": "verified"},
        "merge_check": {
            "decision": "direct",
            "plan_fingerprint": "merge-plan-positive",
        },
        "merge_verify": {"verification_status": "verified"},
    }
    monkeypatch.setattr(
        cli_module,
        "build_dataset_snapshot",
        _result_stub(payloads["snapshot"]),
    )
    monkeypatch.setattr(
        cli_module,
        "diff_dataset_snapshots",
        _result_stub(payloads["diff"]),
    )
    monkeypatch.setattr(
        cli_module,
        "evaluate_dataset_readiness",
        _result_stub(payloads["readiness"]),
    )
    monkeypatch.setattr(
        cli_module,
        "plan_dataset_repair",
        _result_stub(payloads["repair_plan"]),
    )
    monkeypatch.setattr(
        cli_module,
        "apply_dataset_repair",
        _result_stub(payloads["repair_apply"], write_output=False),
    )
    monkeypatch.setattr(
        cli_module,
        "verify_dataset_repair",
        _result_stub(payloads["repair_verify"]),
    )
    monkeypatch.setattr(
        cli_module,
        "check_merge_compatibility",
        _result_stub(payloads["merge_check"]),
    )
    monkeypatch.setattr(
        cli_module,
        "verify_dataset_merge",
        _result_stub(payloads["merge_verify"]),
    )

    runner = CliRunner()
    paths = {
        "snapshot": tmp_path / "snapshot.json",
        "diff": tmp_path / "diff.json",
        "readiness": tmp_path / "readiness.json",
        "repair_plan": tmp_path / "repair-plan.json",
        "repair_apply": tmp_path / "repair-apply.json",
        "repair_verify": tmp_path / "repair-verify.json",
        "merge_check": tmp_path / "merge-plan.json",
        "merge_verify": tmp_path / "merge-receipt.json",
    }
    invocations = {
        "snapshot": [
            "snapshot",
            "dataset",
            "--out",
            str(paths["snapshot"]),
        ],
        "diff": [
            "diff",
            "baseline.json",
            "candidate.json",
            "--out",
            str(paths["diff"]),
        ],
        "readiness": [
            "readiness",
            "dataset",
            "--out",
            str(paths["readiness"]),
        ],
        "repair_plan": [
            "repair",
            "plan",
            "dataset",
            "--out",
            str(paths["repair_plan"]),
        ],
        "repair_apply": [
            "repair",
            "apply",
            "dataset",
            "--plan",
            "repair-plan.json",
            "--output",
            str(tmp_path / "fixed-dataset"),
            "--receipt",
            str(paths["repair_apply"]),
        ],
        "repair_verify": [
            "verify",
            "fixed-dataset",
            "--against",
            "repair-plan.json",
            "--out",
            str(paths["repair_verify"]),
        ],
        "merge_check": [
            "merge-check",
            "first.json",
            "second.json",
            "--out",
            str(paths["merge_check"]),
        ],
        "merge_verify": [
            "verify-merge",
            "merged.json",
            "--input",
            "first.json",
            "--input",
            "second.json",
            "--out",
            str(paths["merge_verify"]),
        ],
    }

    for name, arguments in invocations.items():
        result = runner.invoke(app, arguments)

        assert result.exit_code == 0, (name, result.output, result.exception)
        assert json.loads(paths[name].read_text(encoding="utf-8")) == payloads[name]


def test_v003_cli_completed_negative_results_exit_two_after_writing_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payloads = {
        "diff": {"classification": "material"},
        "readiness": {
            "profile": {"id": "lerobot-core"},
            "status": "BLOCKED",
        },
        "repair_apply": {"status": "unverified"},
        "repair_verify": {"status": "failed"},
        "merge_check": {
            "decision": "transform_required",
            "plan_fingerprint": "merge-plan-negative",
        },
        "merge_verify": {"verification_status": "unverified"},
    }
    monkeypatch.setattr(
        cli_module,
        "diff_dataset_snapshots",
        _result_stub(payloads["diff"]),
    )
    monkeypatch.setattr(
        cli_module,
        "evaluate_dataset_readiness",
        _result_stub(payloads["readiness"]),
    )
    monkeypatch.setattr(
        cli_module,
        "apply_dataset_repair",
        _result_stub(payloads["repair_apply"], write_output=False),
    )
    monkeypatch.setattr(
        cli_module,
        "verify_dataset_repair",
        _result_stub(payloads["repair_verify"]),
    )
    monkeypatch.setattr(
        cli_module,
        "check_merge_compatibility",
        _result_stub(payloads["merge_check"]),
    )
    monkeypatch.setattr(
        cli_module,
        "verify_dataset_merge",
        _result_stub(payloads["merge_verify"]),
    )

    runner = CliRunner()
    paths = {
        name: tmp_path / f"{name}.json"
        for name in payloads
    }
    invocations = {
        "diff": [
            "diff",
            "baseline.json",
            "candidate.json",
            "--out",
            str(paths["diff"]),
            "--fail-on",
            "material",
        ],
        "readiness": [
            "readiness",
            "dataset",
            "--out",
            str(paths["readiness"]),
        ],
        "repair_apply": [
            "repair",
            "apply",
            "dataset",
            "--plan",
            "repair-plan.json",
            "--output",
            str(tmp_path / "fixed-dataset"),
            "--receipt",
            str(paths["repair_apply"]),
        ],
        "repair_verify": [
            "verify",
            "fixed-dataset",
            "--against",
            "repair-plan.json",
            "--out",
            str(paths["repair_verify"]),
        ],
        "merge_check": [
            "merge-check",
            "first.json",
            "second.json",
            "--out",
            str(paths["merge_check"]),
        ],
        "merge_verify": [
            "verify-merge",
            "merged.json",
            "--input",
            "first.json",
            "--input",
            "second.json",
            "--out",
            str(paths["merge_verify"]),
        ],
    }

    for name, arguments in invocations.items():
        result = runner.invoke(app, arguments)

        assert result.exit_code == 2, (name, result.output, result.exception)
        assert json.loads(paths[name].read_text(encoding="utf-8")) == payloads[name]


def test_readiness_partial_gate_can_be_explicitly_allowed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = {
        "profile": {"id": "lerobot-core"},
        "status": "PARTIAL",
    }
    monkeypatch.setattr(
        cli_module,
        "evaluate_dataset_readiness",
        _result_stub(payload),
    )
    runner = CliRunner()
    blocked_output = tmp_path / "partial-blocked.json"
    allowed_output = tmp_path / "partial-allowed.json"

    blocked = runner.invoke(
        app,
        ["readiness", "dataset", "--out", str(blocked_output)],
    )
    allowed = runner.invoke(
        app,
        [
            "readiness",
            "dataset",
            "--out",
            str(allowed_output),
            "--allow-partial",
        ],
    )

    assert blocked.exit_code == 2
    assert allowed.exit_code == 0
    assert json.loads(blocked_output.read_text(encoding="utf-8")) == payload
    assert json.loads(allowed_output.read_text(encoding="utf-8")) == payload


def test_v003_cli_configuration_and_access_errors_exit_one_without_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    cases: list[tuple[str, list[str], Path]] = [
        (
            "build_dataset_snapshot",
            ["snapshot", "dataset", "--out", str(tmp_path / "snapshot.json")],
            tmp_path / "snapshot.json",
        ),
        (
            "diff_dataset_snapshots",
            [
                "diff",
                "baseline.json",
                "candidate.json",
                "--out",
                str(tmp_path / "diff.json"),
            ],
            tmp_path / "diff.json",
        ),
        (
            "evaluate_dataset_readiness",
            ["readiness", "dataset", "--out", str(tmp_path / "readiness.json")],
            tmp_path / "readiness.json",
        ),
        (
            "plan_dataset_repair",
            [
                "repair",
                "plan",
                "dataset",
                "--out",
                str(tmp_path / "repair-plan.json"),
            ],
            tmp_path / "repair-plan.json",
        ),
        (
            "apply_dataset_repair",
            [
                "repair",
                "apply",
                "dataset",
                "--plan",
                "repair-plan.json",
                "--output",
                str(tmp_path / "fixed-dataset"),
                "--receipt",
                str(tmp_path / "repair-apply.json"),
            ],
            tmp_path / "repair-apply.json",
        ),
        (
            "verify_dataset_repair",
            [
                "verify",
                "fixed-dataset",
                "--against",
                "repair-plan.json",
                "--out",
                str(tmp_path / "repair-verify.json"),
            ],
            tmp_path / "repair-verify.json",
        ),
        (
            "check_merge_compatibility",
            [
                "merge-check",
                "first.json",
                "second.json",
                "--out",
                str(tmp_path / "merge-plan.json"),
            ],
            tmp_path / "merge-plan.json",
        ),
        (
            "verify_dataset_merge",
            [
                "verify-merge",
                "merged.json",
                "--input",
                "first.json",
                "--input",
                "second.json",
                "--out",
                str(tmp_path / "merge-receipt.json"),
            ],
            tmp_path / "merge-receipt.json",
        ),
    ]

    for function_name, arguments, artifact_path in cases:
        monkeypatch.setattr(
            cli_module,
            function_name,
            _raising_stub(DatasetArgumentError("invalid source or configuration")),
        )
        result = runner.invoke(app, arguments)

        assert result.exit_code == 1, (
            function_name,
            result.output,
            result.exception,
        )
        assert "Error: invalid source or configuration" in result.output
        assert not artifact_path.exists()


def test_hub_budget_options_are_constructed_and_forwarded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[dict[str, Any]] = []
    snapshot_payload = {"snapshot_fingerprint": "hub-snapshot"}
    readiness_payload = {
        "profile": {"id": "lerobot-core"},
        "status": "READY",
    }
    audit_payload = {
        "summary": {"error": 0, "warning": 0, "info": 0},
    }
    monkeypatch.setattr(
        cli_module,
        "snapshot_hub_dataset",
        _result_stub(snapshot_payload, captured=captured),
    )
    monkeypatch.setattr(
        cli_module,
        "evaluate_hub_dataset_readiness",
        _result_stub(readiness_payload, captured=captured),
    )
    monkeypatch.setattr(
        cli_module,
        "audit_hub_dataset",
        _result_stub(audit_payload, captured=captured),
    )
    runner = CliRunner()
    budget_options = [
        "--hub-max-bytes",
        "101",
        "--hub-max-shards",
        "3",
        "--hub-max-episodes",
        "5",
        "--hub-max-media-shards",
        "2",
    ]

    invocations = [
        [
            "snapshot",
            "hf://datasets/org/name@main",
            "--out",
            str(tmp_path / "snapshot.json"),
            *budget_options,
        ],
        [
            "readiness",
            "hf://datasets/org/name@main",
            "--out",
            str(tmp_path / "readiness.json"),
            *budget_options,
        ],
        [
            "audit",
            "hf://datasets/org/name@main",
            "--out",
            str(tmp_path / "audit.json"),
            *budget_options,
        ],
    ]
    for arguments in invocations:
        result = runner.invoke(app, arguments)
        assert result.exit_code == 0, (result.output, result.exception)

    assert len(captured) == 3
    for call in captured:
        assert call["budget"].as_dict() == {
            "max_bytes": 101,
            "max_shards": 3,
            "max_episodes": 5,
            "max_media_shards": 2,
        }


def test_invalid_hub_budget_is_configuration_exit_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def must_not_run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("Hub operation must not run for an invalid budget")

    monkeypatch.setattr(cli_module, "snapshot_hub_dataset", must_not_run)
    output = tmp_path / "snapshot.json"
    result = CliRunner().invoke(
        app,
        [
            "snapshot",
            "hf://datasets/org/name@main",
            "--out",
            str(output),
            "--hub-max-bytes",
            "0",
        ],
    )

    assert result.exit_code == 1
    assert "max_bytes must be a positive integer" in result.output
    assert not output.exists()


def test_hub_access_error_is_exit_one_without_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "evaluate_hub_dataset_readiness",
        _raising_stub(HubSourceError("Hub access denied")),
    )
    output = tmp_path / "readiness.json"
    result = CliRunner().invoke(
        app,
        [
            "readiness",
            "hf://datasets/org/name@main",
            "--out",
            str(output),
        ],
    )

    assert result.exit_code == 1
    assert "Error: Hub access denied" in result.output
    assert not output.exists()
