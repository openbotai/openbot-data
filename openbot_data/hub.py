"""Revision-pinned, bounded Hugging Face dataset source resolution.

The module deliberately has no dependency on the full ``lerobot`` package.
Network operations are lazy and injectable so callers can use the standard
Hugging Face credential chain in production and deterministic fakes in tests.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

HUB_METADATA_ALLOW_PATTERNS = (
    ".gitattributes",
    "README.md",
    "README.*",
    "meta/**",
)
HUB_FULL_PAYLOAD_ALLOW_PATTERNS = (
    "data/**",
    "videos/**",
)
SUPPORTED_HUB_INTEGRITY = {"metadata", "sample", "full"}
IMMUTABLE_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")
_REPO_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_TOKEN_LIKE = re.compile(
    r"(?:hf_[A-Za-z0-9]{8,}|(?:token|secret|password|authorization)\s*[=:])",
    re.IGNORECASE,
)
_FORMAT_VERSION_REF = re.compile(
    r"^(?:(?:format|lerobot)[-_/])?v?([23])\.(\d+)(?:\.\d+)?$",
    re.IGNORECASE,
)

RevisionResolver = Callable[..., Any]
HubDownloader = Callable[..., Any]
ViewerValidator = Callable[..., Any]

__all__ = [
    "HUB_FULL_PAYLOAD_ALLOW_PATTERNS",
    "HUB_METADATA_ALLOW_PATTERNS",
    "HubArgumentError",
    "HubDependencyError",
    "HubDownloadBudget",
    "HubDownloadError",
    "HubFile",
    "HubResolution",
    "HubRevisionError",
    "HubSourceError",
    "HubSourceRequest",
    "allow_patterns_for_integrity",
    "parse_hub_source",
    "resolve_hub_dataset",
    "resolve_hub_source",
]


class HubSourceError(RuntimeError):
    """Base class for canonical Hub source failures."""


class HubArgumentError(ValueError, HubSourceError):
    """Raised for invalid or potentially secret-bearing caller input."""


class HubDependencyError(HubSourceError):
    """Raised when optional Hugging Face network support is unavailable."""


class HubRevisionError(HubSourceError):
    """Raised when a source cannot be pinned to one immutable commit."""


class HubDownloadError(HubSourceError):
    """Raised when a bounded download cannot produce one local checkout."""


@dataclass(frozen=True)
class HubSourceRequest:
    """Validated dataset repository and caller-requested revision."""

    repo_id: str
    requested_revision: str
    repo_type: str = "dataset"

    @property
    def locator(self) -> str:
        return (
            f"hf://datasets/{self.repo_id}"
            f"@{self.requested_revision}"
        )


@dataclass(frozen=True)
class HubDownloadBudget:
    """Hard limits applied before any payload download starts."""

    max_bytes: int = 2_000_000_000
    max_shards: int = 12
    max_episodes: int = 64
    max_media_shards: int = 9

    def __post_init__(self) -> None:
        for name in (
            "max_bytes",
            "max_shards",
            "max_episodes",
            "max_media_shards",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise HubArgumentError(f"{name} must be a positive integer")

    def as_dict(self) -> dict[str, int]:
        return {
            "max_bytes": self.max_bytes,
            "max_shards": self.max_shards,
            "max_episodes": self.max_episodes,
            "max_media_shards": self.max_media_shards,
        }


@dataclass(frozen=True)
class HubFile:
    """Portable repository inventory entry."""

    path: str
    size_bytes: Optional[int]

    def __post_init__(self) -> None:
        normalized = _portable_repo_path(self.path)
        if normalized != self.path:
            raise HubRevisionError("Hub repository inventory contains an unsafe path")
        if self.size_bytes is not None:
            if (
                isinstance(self.size_bytes, bool)
                or not isinstance(self.size_bytes, int)
                or self.size_bytes < 0
            ):
                raise HubRevisionError(
                    "Hub repository inventory contains an invalid file size"
                )


@dataclass(frozen=True)
class _RepoMetadata:
    sha: str
    files: Tuple[HubFile, ...]
    tags: Tuple[str, ...]
    card_data: Mapping[str, Any]


@dataclass(frozen=True)
class _DownloadOutcome:
    source_path: Path = field(repr=False, compare=False)
    local_path: Path = field(repr=False, compare=False)
    cache_hit: Optional[bool] = None
    resumed: Optional[bool] = None
    resolved_revision: Optional[str] = None
    materialized: bool = False
    cleanup: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class HubResolution:
    """Secret-free Hub provenance, coverage, and a private local checkout."""

    request: HubSourceRequest
    resolved_revision: str
    integrity: str
    metadata_allow_patterns: Tuple[str, ...]
    payload_allow_patterns: Tuple[str, ...]
    provenance: Mapping[str, Any]
    publication_metadata: Mapping[str, Any]
    coverage: Mapping[str, Any]
    findings: Tuple[Mapping[str, Any], ...]
    local_path: Optional[Path] = field(default=None, repr=False, compare=False)
    _cleanup: Any = field(default=None, repr=False, compare=False)

    @property
    def allow_patterns(self) -> Tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (*self.metadata_allow_patterns, *self.payload_allow_patterns)
            )
        )

    def audit_kwargs(self) -> dict[str, Any]:
        """Return private local arguments accepted by ``audit_dataset``."""
        if self.local_path is None:
            raise HubDownloadError(
                "Hub resolution has no local checkout to audit"
            )
        return {
            "path": str(self.local_path),
            "input_format": "lerobot",
            "integrity": self.integrity,
        }

    def snapshot_kwargs(self) -> dict[str, Any]:
        """Return arguments accepted by ``build_dataset_snapshot``."""
        if self.local_path is None:
            raise HubDownloadError(
                "Hub resolution has no local checkout to snapshot"
            )
        return {
            "path": str(self.local_path),
            "input_format": "lerobot",
            "integrity": self.integrity,
            "source_kind": "hf_hub",
            "source_locator": self.request.locator,
            "requested_revision": self.request.requested_revision,
            "resolved_revision": self.resolved_revision,
        }

    def as_dict(self) -> dict[str, Any]:
        """Return canonical public evidence without cache paths or credentials."""
        return {
            "source": _json_copy(self.provenance),
            "publication_metadata": _json_copy(self.publication_metadata),
            "integrity": self.integrity,
            "download": {
                "metadata_allow_patterns": list(self.metadata_allow_patterns),
                "payload_allow_patterns": list(self.payload_allow_patterns),
                "resumable": True,
            },
            "coverage": _json_copy(self.coverage),
            "findings": [_json_copy(item) for item in self.findings],
        }


def _json_copy(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_copy(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_copy(item) for item in value]
    return str(value)


def _reject_secret_like(value: str, label: str) -> None:
    if (
        not value
        or any(ord(character) < 32 for character in value)
        or any(character.isspace() for character in value)
        or _TOKEN_LIKE.search(value)
        or "://" in value
        or "?" in value
        or "#" in value
        or "%" in value
        or "\\" in value
    ):
        raise HubArgumentError(
            f"{label} must be a plain secret-free Hugging Face identifier"
        )


def _normalize_repo_id(value: str) -> str:
    _reject_secret_like(value, "repo_id")
    normalized = value.strip("/")
    if normalized.startswith("datasets/"):
        normalized = normalized[len("datasets/") :]
    parts = normalized.split("/")
    if len(parts) != 2 or any(not _REPO_COMPONENT.fullmatch(part) for part in parts):
        raise HubArgumentError("repo_id must use the form 'owner/name'")
    return "/".join(parts)


def _normalize_revision(value: str) -> str:
    _reject_secret_like(value, "revision")
    if (
        not _REVISION.fullmatch(value)
        or value.startswith("/")
        or value.endswith("/")
        or "//" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise HubArgumentError("revision is not a portable Hub revision")
    return value


def parse_hub_source(
    source: Optional[str] = None,
    *,
    repo_id: Optional[str] = None,
    revision: Optional[str] = None,
) -> HubSourceRequest:
    """Parse ``hf://datasets/owner/name@revision`` or explicit arguments.

    A revision is mandatory. Supplying both forms is allowed only when they
    describe the exact same repository and revision.
    """
    parsed_repo: Optional[str] = None
    parsed_revision: Optional[str] = None
    if source is not None:
        if not isinstance(source, str):
            raise HubArgumentError("Hub source must be a string")
        if _TOKEN_LIKE.search(source):
            raise HubArgumentError("Hub source must not contain credentials")
        parsed = urlsplit(source)
        if (
            parsed.scheme != "hf"
            or parsed.query
            or parsed.fragment
            or "//" in parsed.path
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise HubArgumentError(
                "Hub source must be a secret-free hf:// dataset locator"
            )
        components = [parsed.netloc, *parsed.path.strip("/").split("/")]
        components = [component for component in components if component]
        if components and components[0] == "datasets":
            components = components[1:]
        if len(components) < 2:
            raise HubArgumentError("Hub source has no repository identifier")
        name = components[1]
        if "@" in name:
            name, revision_head = name.split("@", 1)
            parsed_revision = "/".join(
                [revision_head, *components[2:]]
            )
            components = [components[0], name]
        elif len(components) != 2:
            raise HubArgumentError(
                "Hub revision paths must follow the repository name after '@'"
            )
        parsed_repo = _normalize_repo_id("/".join(components))
        if parsed_revision is not None:
            parsed_revision = _normalize_revision(parsed_revision)

    explicit_repo = _normalize_repo_id(repo_id) if repo_id is not None else None
    explicit_revision = (
        _normalize_revision(revision) if revision is not None else None
    )
    if parsed_repo is not None and explicit_repo is not None:
        if parsed_repo != explicit_repo:
            raise HubArgumentError(
                "Hub source and explicit repo_id do not identify the same repository"
            )
    if parsed_revision is not None and explicit_revision is not None:
        if parsed_revision != explicit_revision:
            raise HubArgumentError(
                "Hub source and explicit revision do not match"
            )
    final_repo = parsed_repo or explicit_repo
    final_revision = parsed_revision or explicit_revision
    if final_repo is None:
        raise HubArgumentError("repo_id is required")
    if final_revision is None:
        raise HubArgumentError("A branch, tag, or immutable commit revision is required")
    return HubSourceRequest(
        repo_id=final_repo,
        requested_revision=final_revision,
    )


def allow_patterns_for_integrity(
    integrity: str,
    *,
    selected_payload: Sequence[str] = (),
) -> Tuple[str, ...]:
    """Return the public allow-pattern contract for one integrity level.

    Sample payload paths are selected from the immutable repository inventory
    and therefore must be supplied explicitly.
    """
    if integrity not in SUPPORTED_HUB_INTEGRITY:
        raise HubArgumentError(
            f"integrity must be one of {sorted(SUPPORTED_HUB_INTEGRITY)}"
        )
    if integrity == "metadata":
        payload: Tuple[str, ...] = ()
    elif integrity == "full":
        payload = HUB_FULL_PAYLOAD_ALLOW_PATTERNS
    else:
        normalized = []
        for path in selected_payload:
            candidate = _portable_repo_path(path)
            if candidate is None or not candidate.startswith(("data/", "videos/")):
                raise HubArgumentError(
                    "sample payload patterns must be safe data/ or videos/ paths"
                )
            normalized.append(candidate)
        payload = tuple(sorted(set(normalized)))
    return tuple(dict.fromkeys((*HUB_METADATA_ALLOW_PATTERNS, *payload)))


def _portable_repo_path(value: object) -> Optional[str]:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or value.startswith(("/", "//"))
        or "://" in value
        or "?" in value
        or "#" in value
        or "%" in value
        or _TOKEN_LIKE.search(value)
    ):
        return None
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    normalized = candidate.as_posix()
    return normalized if normalized not in {"", "."} else None


def _value(source: object, *names: str) -> Any:
    for name in names:
        if isinstance(source, Mapping) and name in source:
            return source[name]
        if hasattr(source, name):
            return getattr(source, name)
    return None


def _file_size(item: object) -> Optional[int]:
    value = _value(item, "size", "size_bytes")
    if value is None:
        lfs = _value(item, "lfs")
        value = _value(lfs, "size") if lfs is not None else None
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HubRevisionError("Hub repository inventory contains an invalid size")
    return value


def _coerce_repo_metadata(value: object) -> _RepoMetadata:
    raw_sha = value if isinstance(value, str) else _value(
        value,
        "sha",
        "resolved_revision",
        "commit_sha",
    )
    if not isinstance(raw_sha, str) or not IMMUTABLE_COMMIT.fullmatch(raw_sha):
        raise HubRevisionError(
            "Hub resolver did not return an immutable 40-character commit SHA"
        )
    siblings = () if isinstance(value, str) else (_value(value, "siblings", "files") or ())
    files_by_path: dict[str, HubFile] = {}
    for item in siblings:
        raw_path = _value(item, "rfilename", "path", "name")
        normalized = _portable_repo_path(raw_path)
        if normalized is None:
            raise HubRevisionError("Hub repository inventory contains an unsafe path")
        record = HubFile(normalized, _file_size(item))
        existing = files_by_path.get(normalized)
        if existing is not None and existing != record:
            raise HubRevisionError(
                "Hub repository inventory contains conflicting file metadata"
            )
        files_by_path[normalized] = record
    tags_value = () if isinstance(value, str) else (_value(value, "tags") or ())
    tags = tuple(sorted({str(tag) for tag in tags_value if isinstance(tag, str)}))
    raw_card = {} if isinstance(value, str) else (_value(value, "card_data", "cardData") or {})
    if not isinstance(raw_card, Mapping) and hasattr(raw_card, "to_dict"):
        raw_card = raw_card.to_dict()
    card_data = dict(raw_card) if isinstance(raw_card, Mapping) else {}
    return _RepoMetadata(
        sha=raw_sha.lower(),
        files=tuple(files_by_path[path] for path in sorted(files_by_path)),
        tags=tags,
        card_data=card_data,
    )


def _default_revision_resolver(**kwargs: Any) -> object:
    try:
        from huggingface_hub import HfApi  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - optional production dependency
        raise HubDependencyError(
            "Install huggingface_hub or inject revision_resolver for Hub access"
        ) from exc
    return HfApi().repo_info(
        repo_id=kwargs["repo_id"],
        revision=kwargs["revision"],
        repo_type=kwargs["repo_type"],
        files_metadata=True,
    )


def _default_downloader(**kwargs: Any) -> object:
    try:
        from huggingface_hub import snapshot_download  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - optional production dependency
        raise HubDependencyError(
            "Install huggingface_hub or inject downloader for Hub access"
        ) from exc
    accepted = {
        key: value
        for key, value in kwargs.items()
        if key in {
            "repo_id",
            "revision",
            "repo_type",
            "allow_patterns",
            "cache_dir",
            "local_dir",
        }
        and value is not None
    }
    return snapshot_download(**accepted)


def _download_outcome(value: object, expected_revision: str) -> _DownloadOutcome:
    if isinstance(value, (str, Path)):
        path = Path(value)
        cache_hit = None
        resumed = None
        resolved = None
    elif isinstance(value, Mapping):
        raw_path = value.get("local_path", value.get("path"))
        if not isinstance(raw_path, (str, Path)):
            raise HubDownloadError("Hub downloader did not return a local directory")
        path = Path(raw_path)
        cache_hit = value.get("cache_hit")
        resumed = value.get("resumed")
        resolved = value.get("resolved_revision")
        if cache_hit is not None and not isinstance(cache_hit, bool):
            raise HubDownloadError("Hub downloader returned an invalid cache state")
        if resumed is not None and not isinstance(resumed, bool):
            raise HubDownloadError("Hub downloader returned an invalid resume state")
        if resolved is not None and not isinstance(resolved, str):
            raise HubDownloadError("Hub downloader returned an invalid revision")
    else:
        raise HubDownloadError("Hub downloader did not return a local directory")
    if resolved is not None and resolved.lower() != expected_revision:
        raise HubRevisionError(
            "Hub downloader revision does not match the resolved immutable commit"
        )
    if not path.is_dir():
        raise HubDownloadError("Hub downloader local directory does not exist")
    resolved_path = path.resolve()
    return _DownloadOutcome(
        source_path=resolved_path,
        local_path=resolved_path,
        cache_hit=cache_hit,
        resumed=resumed,
        resolved_revision=resolved,
    )


def _path_has_symlink_parent(candidate: Path, root: Path) -> bool:
    """Return whether a dataset-relative parent component is a symlink."""
    current = candidate.parent
    while current != root:
        if current.is_symlink():
            return True
        parent = current.parent
        if parent == current:
            return True
        current = parent
    return False


def _standard_hf_blob_target(candidate: Path, root: Path) -> Path:
    """Resolve only the standard ``snapshots/<sha> -> blobs/<digest>`` shape."""
    if root.parent.name != "snapshots":
        raise HubDownloadError(
            "Hub checkout contains a symlink outside the standard Hugging Face cache"
        )
    repository_cache = root.parent.parent
    blobs_root = repository_cache / "blobs"
    try:
        target = candidate.resolve(strict=True)
        target.relative_to(blobs_root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as exc:
        raise HubDownloadError(
            "Hub cache symlink does not resolve to a regular repository blob"
        ) from exc
    if not target.is_file() or target.is_symlink():
        raise HubDownloadError(
            "Hub cache symlink does not resolve to a regular repository blob"
        )
    return target


def _materialized_checkout_path(
    root: Path,
    resolved_revision: str,
    selected_paths: Sequence[str],
) -> tuple[Path, Any]:
    repository_cache = root.parent.parent
    digest = hashlib.sha256(
        "\n".join(sorted(selected_paths)).encode("utf-8")
    ).hexdigest()[:16]
    base = repository_cache / ".openbot-data-checkouts"
    try:
        base.mkdir(parents=True, exist_ok=True)
        cleanup = tempfile.TemporaryDirectory(
            prefix=f"{resolved_revision}-{digest}-",
            dir=base,
        )
    except OSError:
        cleanup = tempfile.TemporaryDirectory(
            prefix=f"openbot-data-{resolved_revision}-{digest}-",
        )
    return Path(cleanup.name), cleanup


def _copy_regular_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.openbot-data-{os.getpid()}.tmp"
    )
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise HubDownloadError(
            "Hub cache file could not be materialized into a regular checkout"
        ) from exc


def _validate_and_materialize_download(
    outcome: _DownloadOutcome,
    selected_paths: Sequence[str],
    resolved_revision: str,
) -> tuple[_DownloadOutcome, Tuple[str, ...], Tuple[str, ...]]:
    """Validate exact selected paths and remove standard HF cache symlinks.

    Missing selected files remain explicit coverage gaps. Unsafe or non-standard
    symlinks are rejected because the local adapter must retain its strict
    no-follow boundary.
    """
    normalized = tuple(sorted(set(selected_paths)))
    successful: list[str] = []
    missing: list[str] = []
    symlink_sources: dict[str, Path] = {}
    for relative in normalized:
        portable = _portable_repo_path(relative)
        if portable is None or portable != relative:
            raise HubDownloadError(
                "Hub download validation received an unsafe repository path"
            )
        candidate = outcome.source_path / relative
        if _path_has_symlink_parent(candidate, outcome.source_path):
            raise HubDownloadError(
                "Hub checkout contains a symlinked directory component"
            )
        if candidate.is_symlink():
            symlink_sources[relative] = _standard_hf_blob_target(
                candidate,
                outcome.source_path,
            )
            successful.append(relative)
        elif candidate.is_file():
            successful.append(relative)
        else:
            missing.append(relative)

    if not symlink_sources:
        return outcome, tuple(successful), tuple(missing)

    checkout, cleanup = _materialized_checkout_path(
        outcome.source_path,
        resolved_revision,
        normalized,
    )
    for relative in successful:
        source = symlink_sources.get(
            relative,
            outcome.source_path / relative,
        )
        _copy_regular_file(source, checkout / relative)
    for relative in successful:
        candidate = checkout / relative
        if (
            candidate.is_symlink()
            or _path_has_symlink_parent(candidate, checkout)
            or not candidate.is_file()
        ):
            raise HubDownloadError(
                "Materialized Hub checkout did not produce regular files"
            )
    return (
        _DownloadOutcome(
            source_path=outcome.source_path,
            local_path=checkout.resolve(),
            cache_hit=outcome.cache_hit,
            resumed=outcome.resumed,
            resolved_revision=outcome.resolved_revision,
            materialized=True,
            cleanup=cleanup,
        ),
        tuple(successful),
        tuple(missing),
    )


def _matches_any(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _sample_values(values: Sequence[Any], limit: int = 3) -> Tuple[Any, ...]:
    if not values or limit <= 0:
        return ()
    count = min(limit, len(values))
    if count == 1:
        return (values[0],)
    indexes = {
        int(round(index * (len(values) - 1) / (count - 1)))
        for index in range(count)
    }
    return tuple(values[index] for index in sorted(indexes))


def _camera_key(path: str) -> str:
    parts = PurePosixPath(path).parts
    try:
        start = parts.index("videos") + 1
    except ValueError:
        return "unknown"
    for part in parts[start:-1]:
        if not part.startswith(("chunk-", "file-")):
            return part
    return "unknown"


def _sample_media(files: Sequence[HubFile], limit: int) -> Tuple[HubFile, ...]:
    grouped: dict[str, list[HubFile]] = {}
    for item in files:
        grouped.setdefault(_camera_key(item.path), []).append(item)
    candidates: dict[str, Tuple[HubFile, ...]] = {
        camera: _sample_values(tuple(sorted(items, key=lambda value: value.path)))
        for camera, items in sorted(grouped.items())
    }
    selected = []
    for round_index in range(3):
        for camera in sorted(candidates):
            camera_files = candidates[camera]
            if round_index < len(camera_files):
                item = camera_files[round_index]
                if item not in selected:
                    selected.append(item)
                    if len(selected) == limit:
                        return tuple(selected)
    return tuple(selected)


def _read_info(local_path: Optional[Path]) -> Mapping[str, Any]:
    if local_path is None:
        return {}
    path = local_path / "meta" / "info.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _declared_episode_total(info: Mapping[str, Any]) -> Optional[int]:
    value = info.get("total_episodes")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _version_tuple(value: object) -> Optional[Tuple[int, int]]:
    if not isinstance(value, str):
        return None
    candidate = value.strip().split("/")[-1]
    match = _FORMAT_VERSION_REF.fullmatch(candidate)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _validate_format_version(
    requested_revision: str,
    info: Mapping[str, Any],
) -> None:
    requested = _version_tuple(requested_revision)
    declared = _version_tuple(info.get("codebase_version"))
    if requested is not None and declared is not None and requested != declared:
        raise HubRevisionError(
            "Hub version revision is inconsistent with meta/info.json codebase_version"
        )


def _download_state(
    outcome: Optional[_DownloadOutcome],
    patterns: Sequence[str],
    *,
    requested: bool,
    selected_paths: Sequence[str] = (),
    successful_paths: Sequence[str] = (),
    missing_paths: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "requested": requested,
        "completed": outcome is not None,
        "allow_patterns": list(patterns),
        "selected_paths": list(selected_paths),
        "successful_paths": list(successful_paths),
        "missing_paths": list(missing_paths),
        "validated": (
            outcome is not None
            and bool(selected_paths)
            and not missing_paths
            and set(successful_paths) == set(selected_paths)
        ),
    }


def _download_arguments(
    request: HubSourceRequest,
    resolved_revision: str,
    patterns: Sequence[str],
    cache_dir: Optional[str],
    local_dir: Optional[str],
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "repo_id": request.repo_id,
        "revision": resolved_revision,
        "repo_type": request.repo_type,
        "allow_patterns": list(patterns),
    }
    if cache_dir is not None:
        arguments["cache_dir"] = cache_dir
    if local_dir is not None:
        arguments["local_dir"] = local_dir
    return arguments


def _finding(
    code: str,
    message: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "warning",
        "layer": "provenance",
        "message": message,
        "path": ".",
        "location": {},
        "evidence": _json_copy(evidence),
    }


def _publication_missing(metadata: _RepoMetadata) -> Tuple[str, ...]:
    publication = _publication_metadata(metadata)
    missing = []
    if not publication.get("license"):
        missing.append("license")
    if not publication.get("tags"):
        missing.append("tags")
    if not publication.get("task_categories"):
        missing.append("task_categories")
    return tuple(sorted(missing))


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return sorted(
        {
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        }
    )


def _publication_metadata(metadata: _RepoMetadata) -> dict[str, Any]:
    """Return the small, policy-relevant subset of Hub card metadata."""
    raw_license = metadata.card_data.get("license")
    license_value = (
        raw_license.strip()
        if isinstance(raw_license, str) and raw_license.strip()
        else None
    )
    card_tags = _string_list(metadata.card_data.get("tags"))
    tags = card_tags or list(metadata.tags)
    return {
        "license": license_value,
        "tags": tags,
        "task_categories": _string_list(
            metadata.card_data.get("task_categories")
        ),
    }


def _known_size(files: Sequence[HubFile]) -> Optional[int]:
    if any(item.size_bytes is None for item in files):
        return None
    return sum(int(item.size_bytes or 0) for item in files)


def _payload_plan(
    integrity: str,
    files: Sequence[HubFile],
    budget: HubDownloadBudget,
    remaining_bytes: int,
    episode_total: Optional[int],
) -> Tuple[Tuple[str, ...], Tuple[HubFile, ...], Tuple[str, ...]]:
    data = tuple(
        item for item in files if item.path.startswith("data/")
    )
    media = tuple(
        item for item in files if item.path.startswith("videos/")
    )
    exhausted = []
    if integrity == "metadata":
        return (), (), ()
    if integrity == "sample":
        desired_data = _sample_values(data, 3)
        desired_camera_coverage = _sample_media(
            media,
            max(
                1,
                len({_camera_key(item.path) for item in media}),
            ),
        )
        if len(desired_data) + len(desired_camera_coverage) > budget.max_shards:
            exhausted.append("max_shards")
        selected_data = desired_data[: budget.max_shards]
        remaining_shards = max(0, budget.max_shards - len(selected_data))
        selected_media = _sample_media(
            media,
            min(budget.max_media_shards, remaining_shards),
        )
        if len(selected_media) < len(desired_camera_coverage):
            exhausted.append("max_media_shards")
        selected = tuple(selected_data) + tuple(selected_media)
        accepted = []
        consumed = 0
        for item in selected:
            if item.size_bytes is None:
                exhausted.append("max_bytes_unknown_size")
                break
            if consumed + item.size_bytes > remaining_bytes:
                exhausted.append("max_bytes")
                break
            accepted.append(item)
            consumed += item.size_bytes
        if len(selected) > budget.max_shards:
            exhausted.append("max_shards")
        return (
            tuple(item.path for item in accepted),
            tuple(accepted),
            tuple(sorted(set(exhausted))),
        )

    payload = tuple(sorted((*data, *media), key=lambda item: item.path))
    payload_size = _known_size(payload)
    if len(payload) > budget.max_shards:
        exhausted.append("max_shards")
    if len(media) > budget.max_media_shards:
        exhausted.append("max_media_shards")
    if episode_total is None:
        exhausted.append("max_episodes_unknown_total")
    elif episode_total > budget.max_episodes:
        exhausted.append("max_episodes")
    if payload_size is None:
        exhausted.append("max_bytes_unknown_size")
    elif payload_size > remaining_bytes:
        exhausted.append("max_bytes")
    if exhausted:
        return (), (), tuple(sorted(set(exhausted)))
    return HUB_FULL_PAYLOAD_ALLOW_PATTERNS, payload, ()


def _episodes_for_successful_payload(
    local_path: Optional[Path],
    data_paths: Sequence[str],
    media_paths: Sequence[str],
    max_episodes: int,
) -> Tuple[int, ...]:
    """Project sample episodes only from relations to present selected payload."""
    if local_path is None or (not data_paths and not media_paths):
        return ()
    try:
        from openbot_data.adapters import (
            DiscoveryRequest,
            read_lerobot_dataset,
        )

        adapter = read_lerobot_dataset(
            str(local_path),
            DiscoveryRequest(
                integrity="metadata",
                checksum=None,
                follow_symlinks=False,
            ),
        )
    except Exception:
        return ()
    selected = set(data_paths) | set(media_paths)
    episode_indexes = tuple(
        sorted(
            {
                relation.episode_index
                for relation in adapter.relations
                if relation.exists and relation.path in selected
            }
        )
    )
    return tuple(_sample_values(episode_indexes, max_episodes))


def resolve_hub_dataset(
    source: Optional[str] = None,
    *,
    repo_id: Optional[str] = None,
    revision: Optional[str] = None,
    integrity: str = "metadata",
    budget: Optional[HubDownloadBudget] = None,
    resolver: Optional[RevisionResolver] = None,
    revision_resolver: Optional[RevisionResolver] = None,
    downloader: Optional[HubDownloader] = None,
    viewer_validator: Optional[ViewerValidator] = None,
    cache_dir: Optional[str] = None,
    local_dir: Optional[str] = None,
    download: bool = True,
) -> HubResolution:
    """Resolve and optionally download one immutable Hub dataset revision.

    ``revision_resolver`` and ``downloader`` receive keyword arguments and must
    not require a token. The default implementations use the normal Hugging
    Face environment/keychain credential resolution.
    """
    if integrity not in SUPPORTED_HUB_INTEGRITY:
        raise HubArgumentError(
            f"integrity must be one of {sorted(SUPPORTED_HUB_INTEGRITY)}"
        )
    request = parse_hub_source(
        source,
        repo_id=repo_id,
        revision=revision,
    )
    effective_budget = budget or HubDownloadBudget()
    if resolver is not None and revision_resolver is not None:
        raise HubArgumentError(
            "Pass only one of resolver or revision_resolver"
        )
    active_resolver = resolver or revision_resolver or _default_revision_resolver
    try:
        raw_metadata = active_resolver(
            repo_id=request.repo_id,
            revision=request.requested_revision,
            repo_type=request.repo_type,
        )
    except HubSourceError:
        raise
    except Exception as exc:
        raise HubRevisionError(
            "Hub repository metadata resolution failed"
        ) from exc
    metadata = _coerce_repo_metadata(raw_metadata)
    if (
        IMMUTABLE_COMMIT.fullmatch(request.requested_revision)
        and request.requested_revision.lower() != metadata.sha
    ):
        raise HubRevisionError(
            "Requested immutable revision does not match the resolved commit"
        )

    metadata_files = tuple(
        item
        for item in metadata.files
        if _matches_any(item.path, HUB_METADATA_ALLOW_PATTERNS)
    )
    metadata_size = _known_size(metadata_files)
    exhausted = []
    if not metadata.files:
        exhausted.append("repository_inventory_unavailable")
    if metadata_size is None:
        exhausted.append("max_bytes_unknown_metadata_size")
    elif metadata_size > effective_budget.max_bytes:
        exhausted.append("max_bytes")

    active_downloader = downloader or _default_downloader
    metadata_outcome: Optional[_DownloadOutcome] = None
    metadata_selected_paths = tuple(item.path for item in metadata_files)
    metadata_successful_paths: Tuple[str, ...] = ()
    metadata_missing_paths: Tuple[str, ...] = ()
    if download and not exhausted:
        try:
            raw_download = active_downloader(
                **_download_arguments(
                    request,
                    metadata.sha,
                    HUB_METADATA_ALLOW_PATTERNS,
                    cache_dir,
                    local_dir,
                )
            )
        except HubSourceError:
            raise
        except Exception as exc:
            raise HubDownloadError("Hub metadata download failed") from exc
        metadata_outcome = _download_outcome(raw_download, metadata.sha)
        (
            metadata_outcome,
            metadata_successful_paths,
            metadata_missing_paths,
        ) = _validate_and_materialize_download(
            metadata_outcome,
            metadata_selected_paths,
            metadata.sha,
        )
    info = _read_info(metadata_outcome.local_path if metadata_outcome else None)
    _validate_format_version(request.requested_revision, info)
    episode_total = _declared_episode_total(info)
    remaining_bytes = max(
        0,
        effective_budget.max_bytes - int(metadata_size or 0),
    )
    payload_patterns, selected_payload, payload_exhausted = _payload_plan(
        integrity,
        metadata.files,
        effective_budget,
        remaining_bytes,
        episode_total,
    )
    exhausted.extend(payload_exhausted)

    payload_outcome: Optional[_DownloadOutcome] = None
    payload_successful_paths: Tuple[str, ...] = ()
    payload_missing_paths: Tuple[str, ...] = ()
    can_download_payload = not payload_exhausted or integrity == "sample"
    if (
        download
        and metadata_outcome is not None
        and payload_patterns
        and can_download_payload
    ):
        try:
            raw_download = active_downloader(
                **_download_arguments(
                    request,
                    metadata.sha,
                    payload_patterns,
                    cache_dir,
                    local_dir,
                )
            )
        except HubSourceError:
            raise
        except Exception as exc:
            raise HubDownloadError("Hub payload download failed") from exc
        payload_outcome = _download_outcome(raw_download, metadata.sha)
        if payload_outcome.source_path != metadata_outcome.source_path:
            raise HubDownloadError(
                "Metadata and payload downloads did not produce one checkout"
            )
        combined_paths = tuple(
            dict.fromkeys(
                (*metadata_successful_paths, *(item.path for item in selected_payload))
            )
        )
        (
            payload_outcome,
            combined_successful,
            combined_missing,
        ) = _validate_and_materialize_download(
            payload_outcome,
            combined_paths,
            metadata.sha,
        )
        payload_plan_paths = {
            item.path for item in selected_payload
        }
        payload_successful_paths = tuple(
            path for path in combined_successful if path in payload_plan_paths
        )
        payload_missing_paths = tuple(
            path for path in combined_missing if path in payload_plan_paths
        )
        metadata_missing_after_payload = tuple(
            path for path in combined_missing if path in metadata_successful_paths
        )
        if metadata_missing_after_payload:
            metadata_missing_paths = tuple(
                sorted(
                    set(metadata_missing_paths) | set(metadata_missing_after_payload)
                )
            )
            metadata_successful_paths = tuple(
                path
                for path in metadata_successful_paths
                if path not in set(metadata_missing_after_payload)
            )
        metadata_outcome = _DownloadOutcome(
            source_path=metadata_outcome.source_path,
            local_path=payload_outcome.local_path,
            cache_hit=metadata_outcome.cache_hit,
            resumed=metadata_outcome.resumed,
            resolved_revision=metadata_outcome.resolved_revision,
            materialized=payload_outcome.materialized,
            cleanup=payload_outcome.cleanup,
        )

    selected_data = tuple(
        path for path in payload_successful_paths if path.startswith("data/")
    )
    selected_media = tuple(
        path for path in payload_successful_paths if path.startswith("videos/")
    )
    selected_cameras = tuple(sorted({_camera_key(path) for path in selected_media}))
    selected_episodes = (
        _episodes_for_successful_payload(
            payload_outcome.local_path if payload_outcome is not None else None,
            selected_data,
            selected_media,
            effective_budget.max_episodes,
        )
        if integrity == "sample"
        else (
            tuple(range(episode_total))
            if (
                episode_total is not None
                and integrity == "full"
                and payload_outcome is not None
                and not payload_missing_paths
                and not exhausted
            )
            else ()
        )
    )

    completed = ["source.identity", "provenance.revision", "hub.repository_metadata"]
    if metadata_outcome is not None and not metadata_missing_paths:
        completed.append("metadata.download")
    if info:
        completed.append("metadata.info")
    if payload_outcome is not None:
        if integrity == "sample":
            if selected_data and not any(
                path.startswith("data/") for path in payload_missing_paths
            ):
                completed.append("data.sample")
            if selected_media and not any(
                path.startswith("videos/") for path in payload_missing_paths
            ):
                completed.append("media.sample")
        elif integrity == "full":
            if selected_data and not any(
                path.startswith("data/") for path in payload_missing_paths
            ):
                completed.append("data.full")
            if selected_media and not any(
                path.startswith("videos/") for path in payload_missing_paths
            ):
                completed.append("media.full")

    skipped = []
    if not download:
        skipped.append(
            {
                "capability": "source.download",
                "reason_code": "download_not_requested",
                "reason": "The caller requested a revision-resolution plan only.",
            }
        )
    if metadata_outcome is None:
        skipped.append(
            {
                "capability": "metadata.download",
                "reason_code": (
                    "download_not_requested"
                    if not download
                    else "budget_exhausted"
                ),
                "reason": (
                    "The caller requested a revision-resolution plan only."
                    if not download
                    else "Metadata download stopped before exceeding a budget."
                ),
            }
        )
    elif not info:
        skipped.append(
            {
                "capability": "metadata.info",
                "reason_code": "artifact_missing_or_unreadable",
                "reason": "The downloaded checkout has no readable meta/info.json.",
            }
        )
    if metadata_missing_paths:
        skipped.append(
            {
                "capability": "metadata.download",
                "reason_code": "selected_artifact_missing",
                "reason": (
                    "One or more selected metadata artifacts were not present "
                    "after the Hub download completed."
                ),
            }
        )
    if integrity == "metadata":
        skipped.extend(
            [
                {
                    "capability": "data.rows",
                    "reason_code": "integrity_too_low",
                    "reason": "Metadata integrity does not download data payload shards.",
                },
                {
                    "capability": "media.decode",
                    "reason_code": "integrity_too_low",
                    "reason": "Metadata integrity does not download or decode media payloads.",
                },
                {
                    "capability": "statistics.recompute",
                    "reason_code": "integrity_too_low",
                    "reason": "Metadata integrity does not recompute payload statistics.",
                },
            ]
        )
    elif integrity == "sample":
        skipped.extend(
            [
                {
                    "capability": "data.rows.full",
                    "reason_code": "sampled_coverage",
                    "reason": "Sample integrity checks only selected data shards.",
                },
                {
                    "capability": "media.decode.full",
                    "reason_code": "sampled_coverage",
                    "reason": "Sample integrity checks only selected media shards.",
                },
                {
                    "capability": "statistics.recompute",
                    "reason_code": "sampled_coverage",
                    "reason": "Sample integrity cannot establish full-dataset statistics.",
                },
            ]
        )
        if payload_missing_paths:
            missing_data = any(
                path.startswith("data/") for path in payload_missing_paths
            )
            missing_media = any(
                path.startswith("videos/") for path in payload_missing_paths
            )
            for capability, applicable in (
                ("data.sample", missing_data),
                ("media.sample", missing_media),
            ):
                if applicable:
                    skipped.append(
                        {
                            "capability": capability,
                            "reason_code": "selected_artifact_missing",
                            "reason": (
                                "A selected sample artifact was not present "
                                "after the Hub download completed."
                            ),
                        }
                    )
        if (selected_data or selected_media) and not selected_episodes:
            skipped.append(
                {
                    "capability": "episodes.sample",
                    "reason_code": "payload_relation_unavailable",
                    "reason": (
                        "No downloaded episode metadata relation tied the "
                        "successful sample payload to an episode."
                    ),
                }
            )
    if exhausted:
        for capability in (
            "data.rows" if integrity == "full" else "data.sample",
            "media.decode" if integrity == "full" else "media.sample",
        ):
            skipped.append(
                {
                    "capability": capability,
                    "reason_code": "budget_exhausted",
                    "reason": "The requested payload phase stopped before exceeding a budget.",
                }
            )

    viewer = None
    if viewer_validator is not None:
        try:
            raw_viewer = viewer_validator(
                repo_id=request.repo_id,
                revision=metadata.sha,
                repo_type=request.repo_type,
            )
        except Exception:
            raw_viewer = None
        if isinstance(raw_viewer, bool):
            viewer = {"status": "complete", "is_valid": raw_viewer}
        elif isinstance(raw_viewer, Mapping) and isinstance(
            raw_viewer.get("is_valid"),
            bool,
        ):
            viewer = {
                "status": "complete",
                "is_valid": raw_viewer["is_valid"],
            }
        else:
            viewer = {"status": "unavailable", "is_valid": None}

    exhausted_limits = tuple(sorted(set(exhausted)))
    status = (
        "complete"
        if (
            integrity == "full"
            and download
            and metadata_outcome is not None
            and bool(info)
            and not metadata_missing_paths
            and payload_outcome is not None
            and not payload_missing_paths
            and not exhausted_limits
        )
        else "partial"
    )
    publication_missing = _publication_missing(metadata)
    publication_metadata = _publication_metadata(metadata)
    findings = []
    if skipped:
        findings.append(
            _finding(
                "HUB_PARTIAL_COVERAGE",
                "Hub audit did not complete every validation capability.",
                {
                    "requested_integrity": integrity,
                    "skipped_capabilities": sorted(
                        {item["capability"] for item in skipped}
                    ),
                },
            )
        )
    if exhausted_limits:
        findings.append(
            _finding(
                "HUB_DOWNLOAD_BUDGET_EXHAUSTED",
                "Hub download stopped before exceeding an effective budget.",
                {
                    "exhausted_limits": list(exhausted_limits),
                    "effective_budget": effective_budget.as_dict(),
                },
            )
        )
    if publication_missing:
        findings.append(
            _finding(
                "HUB_PUBLICATION_METADATA_MISSING",
                "Optional Hub publication metadata is incomplete.",
                {"missing": list(publication_missing)},
            )
        )

    provenance = {
        "kind": "hf_hub",
        "repo_type": request.repo_type,
        "locator": request.locator,
        "repo_id": request.repo_id,
        "requested_revision": request.requested_revision,
        "resolved_revision": metadata.sha,
    }
    coverage = {
        "status": status,
        "validation_scope": (
            "metadata_validated"
            if (
                integrity == "metadata"
                and metadata_outcome is not None
                and info
                and not metadata_missing_paths
            )
            else (
                "sample_validated"
                if (
                    integrity == "sample"
                    and payload_outcome is not None
                    and bool(payload_successful_paths)
                    and not payload_missing_paths
                )
                else (
                    "full_validated"
                    if status == "complete"
                    else "partial"
                )
            )
        ),
        "requested_integrity": integrity,
        "completed_capabilities": sorted(set(completed)),
        "skipped_capabilities": sorted(
            skipped,
            key=lambda item: (item["capability"], item["reason_code"]),
        ),
        "selection": {
            "episodes": list(selected_episodes),
            "max_episodes": effective_budget.max_episodes,
            "metadata_shards": list(metadata_successful_paths),
            "data_shards": list(selected_data),
            "cameras": list(selected_cameras),
            "media_shards": list(selected_media),
        },
        "totals": {
            "episodes": episode_total,
            "metadata_shards": len(metadata_files),
            "data_shards": sum(
                item.path.startswith("data/") for item in metadata.files
            ),
            "cameras": len(
                {
                    _camera_key(item.path)
                    for item in metadata.files
                    if item.path.startswith("videos/")
                }
            ),
            "media_shards": sum(
                item.path.startswith("videos/") for item in metadata.files
            ),
        },
        "bytes": {
            "metadata_selected": metadata_size,
            "payload_selected": _known_size(
                tuple(
                    item
                    for item in selected_payload
                    if item.path in set(payload_successful_paths)
                )
            ),
            "repository_known": _known_size(metadata.files),
        },
        "effective_budget": effective_budget.as_dict(),
        "exhausted_limits": list(exhausted_limits),
        "downloads": {
            "metadata": _download_state(
                metadata_outcome,
                HUB_METADATA_ALLOW_PATTERNS,
                requested=download,
                selected_paths=metadata_selected_paths,
                successful_paths=metadata_successful_paths,
                missing_paths=metadata_missing_paths,
            ),
            "payload": _download_state(
                payload_outcome,
                payload_patterns,
                requested=download and integrity in {"sample", "full"},
                selected_paths=tuple(item.path for item in selected_payload),
                successful_paths=payload_successful_paths,
                missing_paths=payload_missing_paths,
            ),
        },
        "dataset_viewer": viewer,
    }
    local_path = (
        payload_outcome.local_path
        if payload_outcome is not None
        else (
            metadata_outcome.local_path
            if metadata_outcome is not None
            else None
        )
    )
    return HubResolution(
        request=request,
        resolved_revision=metadata.sha,
        integrity=integrity,
        metadata_allow_patterns=HUB_METADATA_ALLOW_PATTERNS,
        payload_allow_patterns=tuple(payload_patterns),
        provenance=provenance,
        publication_metadata=publication_metadata,
        coverage=coverage,
        findings=tuple(
            sorted(
                findings,
                key=lambda item: (
                    str(item["code"]),
                    json.dumps(item["evidence"], sort_keys=True),
                ),
            )
        ),
        local_path=local_path,
        _cleanup=(
            payload_outcome.cleanup
            if payload_outcome is not None
            else (
                metadata_outcome.cleanup
                if metadata_outcome is not None
                else None
            )
        ),
    )


resolve_hub_source = resolve_hub_dataset
