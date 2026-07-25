# API and CLI reference

All public functions are importable from `openbot_data`.

## Dataset preflight

### `inspect_dataset`

```python
inspect_dataset(
    video_dir: str,
    output_dir: str,
    input_format: str = "video",
    checksum: str | None = None,
    *,
    integrity: str = "sample",
    follow_symlinks: bool = False,
    snapshot: DatasetSnapshot | None = None,
) -> dict
```

Scans the input, extracts previews, and writes
`metadata/manifest.json` plus `metadata/report.json`. Set `input_format` to
`"auto"`, `"video"`, or `"lerobot"`. The only supported checksum is
`"sha256"`. `integrity` is `metadata`, `sample`, or `full`; `full` decodes every
frame. Passing a snapshot from `prepare_dataset` avoids a second scan/hash pass.
Symlinks are skipped by default and can never escape the dataset root. The
returned dictionary includes `manifest_path`, `report_path`,
`total_videos`, `total_previews`, and `dataset_fingerprint`; input failures
return an `error` field.

### `audit_dataset`

```python
audit_dataset(
    path: str,
    input_format: str = "auto",
    checksum: str | None = None,
    output_path: str | None = None,
    *,
    integrity: str = "sample",
    follow_symlinks: bool = False,
    snapshot: DatasetSnapshot | None = None,
) -> dict
```

Returns an `openbot.dataset_audit.v1` dictionary:

```json
{
  "schema_version": "openbot.dataset_audit.v1",
  "input_format": "video",
  "summary": {"videos": 1, "error": 0, "warning": 0, "info": 0},
  "findings": []
}
```

Malformed inputs are represented as structured findings rather than raw
tracebacks. If `output_path` is supplied, canonical JSON is also written there.
See [audit finding codes](audit-findings.md).

### `prepare_dataset`

```python
prepare_dataset(
    path: str,
    input_format: str = "auto",
    checksum: str | None = None,
    *,
    integrity: str = "sample",
    follow_symlinks: bool = False,
) -> DatasetSnapshot
```

Builds the immutable typed discovery model used by manifest, preview, and audit
renderers. `DatasetSnapshot` contains typed `EpisodeRecord` and `VideoRecord`
tuples; private `source_path` values are not emitted by `as_dict()`.

### `schema_path`

```python
with schema_path("manifest") as path:
    ...
with schema_path("audit") as path:
    ...
```

Yields the packaged Draft 2020-12 JSON Schema for
`openbot.dataset_manifest.v1` or `openbot.dataset_audit.v1`.

### `detect_input_format`

```python
detect_input_format(path: str, input_format: str = "auto") -> str
```

Returns `"video"` or `"lerobot"`. An unsupported requested format raises
`ValueError`.

### `read_lerobot`

```python
read_lerobot(path: str) -> dict
```

Returns `format`, `codebase_version`, sorted `episodes`, `video_keys`, `videos`,
and `findings`. Each episode includes `episode_index`, `length`, `tasks`, and
`video_files`. LeRobot v3 is resolved from the relational
`video_key/chunk_index/file_index/from_timestamp/to_timestamp` metadata and the
declared `info.video_path` template; shared video shards are represented as
`video_segments` rather than guessed per-episode filenames. Parquet episode
metadata requires `openbot-data[lerobot]`.

## Video inspection

### `scan_video`

```python
scan_video(video_path: str) -> VideoInfo
```

Returns a `VideoInfo` dataclass with path, filename, width, height, FPS, frame
count, duration, size, validity, and a safe error string.

### `scan_directory`

```python
scan_directory(directory: str) -> dict
```

Recursively scans supported video extensions and returns aggregate counts,
duration, size, and per-video metadata. A missing directory returns an `error`
field and an empty `videos` list.

### `extract_preview_frames`

```python
extract_preview_frames(
    video_path: str,
    output_dir: str,
    max_frames: int = 10,
    output_id: str | None = None,
) -> dict
```

Uniformly extracts preview frames. `output_id` prevents collisions when
different source directories contain the same filename.

### `extract_timestamped_frames`

```python
extract_timestamped_frames(
    video_path: str,
    output_dir: str,
    sample_fps: float = 1.0,
    max_frames: int = 32,
    max_edge: int = 640,
) -> dict
```

Extracts time-based evidence frames with explicit timestamp and decode-failure
metadata. Invalid numeric arguments return an `error` field.

### `build_contact_sheets`

```python
build_contact_sheets(
    frames: list[dict],
    output_dir: str,
    columns: int = 5,
    rows: int = 4,
    tile_width: int = 320,
) -> dict
```

Builds timestamped contact sheets from successfully decoded frame records.

## Catalog export

### `export_catalog`

```python
export_catalog(
    video_dir: str,
    output_path: str,
    fmt: str = "json",
) -> dict
```

Writes JSON or CSV and returns the output path, format, and video count.
Unsupported formats raise `ValueError`.

## CLI commands

| Command | Purpose | Key options |
|---|---|---|
| `scan PATH` | Scan videos and print/write metadata | `--output FILE` |
| `inspect PATH` | Write previews, manifest, and report | `--out DIR`, `--format`, `--checksum`, `--integrity`, `--follow-symlinks` |
| `audit PATH` | Write structured findings | `--out FILE`, `--format`, `--checksum`, `--integrity`, `--follow-symlinks`, `--fail-on` |
| `catalog PATH` | Export JSON/CSV catalog | `--out FILE`, `--format` |
| `version` | Print installed package version | none |

Run `openbot-data COMMAND --help` for the installed command syntax.
