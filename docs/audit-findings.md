# OpenBot Data Audit Findings

`openbot.dataset_audit.v1` reports evidence-backed findings instead of an
uncalibrated numeric quality score.

| Code | Severity | Meaning |
|---|---|---|
| `DATASET_NOT_FOUND` | error | The requested dataset directory does not exist. |
| `DATASET_INVALID_ARGUMENT` | error | Input format or checksum configuration is unsupported. |
| `DATASET_PATH_OUTSIDE_ROOT` | error | A media path or symlink resolves outside the dataset root. |
| `DATASET_SYMLINK_SKIPPED` | warning | A media symlink was intentionally skipped because symlink following was not enabled. |
| `DATASET_SYMLINK_BROKEN` | error | An opted-in media symlink does not resolve to a file. |
| `HUB_PARTIAL_COVERAGE` | warning | A revision-pinned Hub audit intentionally checked only part of the dataset. |
| `HUB_DOWNLOAD_BUDGET_EXHAUSTED` | warning | A Hub payload download stopped before exceeding a configured budget. |
| `HUB_PUBLICATION_METADATA_MISSING` | warning | Optional Hub publication metadata is missing and needs review. |
| `LEROBOT_INFO_MISSING` | error | `meta/info.json` is missing. |
| `LEROBOT_CODEBASE_VERSION_MISSING` | error | `info.json.codebase_version` is missing, so no format adapter can be selected. |
| `LEROBOT_CODEBASE_VERSION_INVALID` | error | `codebase_version` is not a valid supported version syntax. |
| `LEROBOT_CODEBASE_VERSION_UNTESTED` | warning | A same-major adapter is being used provisionally for an unknown minor or patch version. |
| `LEROBOT_CODEBASE_VERSION_UNSUPPORTED` | error | The declared LeRobot major version has no compatible adapter. |
| `LEROBOT_V21_MIGRATION_RECOMMENDED` | info | The v2.1 dataset remains read-only; migrate a reviewed copy with LeRobot 0.6.0's official v2.1-to-v3.0 converter before v3-only training or merge operations. |
| `LEROBOT_LAYOUT_VERSION_MISMATCH` | error | Dataset files do not match the declared LeRobot storage-contract version. |
| `LEROBOT_METADATA_INVALID` | error | LeRobot JSON metadata cannot be parsed as an object. |
| `LEROBOT_TASKS_MISSING` | error | Required LeRobot task metadata is missing. |
| `LEROBOT_TASKS_UNREADABLE` | error | Task JSONL/Parquet metadata cannot be read. |
| `LEROBOT_TASK_INVALID` | error | A task metadata record is malformed. |
| `LEROBOT_STATS_INVALID` | error | Normalization statistics metadata cannot be parsed as an object. |
| `LEROBOT_EPISODES_MISSING` | error | No JSONL or parquet episode metadata exists. |
| `LEROBOT_EPISODES_UNREADABLE` | error | Episode JSONL/parquet cannot be read. |
| `LEROBOT_DEPENDENCY_MISSING` | error | Parquet discovery needs the optional `lerobot` extra. |
| `LEROBOT_EPISODE_INVALID` | error | An episode JSONL record is malformed. |
| `LEROBOT_EPISODE_INDEX_INVALID` | error | An episode has no valid integer index. |
| `LEROBOT_EPISODE_LENGTH_INVALID` | error | An episode length is not a non-negative integer. |
| `LEROBOT_EPISODE_INDEX_DUPLICATE` | error | An episode index occurs in more than one metadata record. |
| `LEROBOT_EPISODE_INDEX_NON_CONTIGUOUS` | error | Episode indexes contain a gap. |
| `LEROBOT_EPISODE_COUNT_MISMATCH` | error | Declared and discovered episode counts differ. |
| `LEROBOT_FRAME_COUNT_MISMATCH` | error | Declared and validated frame counts differ. |
| `LEROBOT_TASK_COUNT_MISMATCH` | error | Declared and validated task counts differ. |
| `LEROBOT_VIDEO_COUNT_MISMATCH` | error | Declared and validated video counts differ. |
| `LEROBOT_DATA_SHARD_COUNT_MISMATCH` | error | Declared and validated data-shard counts differ. |
| `LEROBOT_EPISODE_RANGE_INVALID` | error | An episode data range is invalid. |
| `LEROBOT_EPISODE_RANGE_LENGTH_MISMATCH` | error | An episode range does not match its declared length. |
| `LEROBOT_EPISODE_RANGE_GAP` | error | Episode ranges leave uncovered rows. |
| `LEROBOT_EPISODE_RANGE_OVERLAP` | error | Episode ranges overlap. |
| `LEROBOT_EPISODE_RANGE_OUT_OF_BOUNDS` | error | An episode range extends beyond its data shard. |
| `LEROBOT_DATA_PATH_TEMPLATE_INVALID` | error | The declared data path template is malformed or lacks required placeholders. |
| `LEROBOT_DATA_RELATION_MISSING` | error | A LeRobot v3 episode lacks a complete data-shard relation. |
| `LEROBOT_DATA_RELATION_INVALID` | error | A LeRobot v3 data-shard relation contains invalid indexes. |
| `LEROBOT_DATA_MISSING` | error | A required or referenced data shard is missing. |
| `LEROBOT_DATA_UNREADABLE` | error | A data Parquet footer or row group cannot be read. |
| `LEROBOT_PARQUET_UNFINALIZED` | error | A Parquet artifact appears incomplete or unfinalized. |
| `LEROBOT_PARQUET_SCHEMA_UNREADABLE` | error | A Parquet schema cannot be read. |
| `LEROBOT_PARQUET_ROW_GROUP_UNREADABLE` | error | A Parquet row group cannot be read. |
| `LEROBOT_PARQUET_ROW_INVALID` | error | A Parquet row is malformed. |
| `LEROBOT_FEATURE_COLUMN_MISSING` | error | A declared feature column is missing. |
| `LEROBOT_FEATURE_COLUMN_UNDECLARED` | warning | A payload column is not declared by the feature contract. |
| `LEROBOT_FEATURE_DTYPE_MISMATCH` | error | A feature dtype differs from its declaration. |
| `LEROBOT_FEATURE_SHAPE_MISMATCH` | error | A feature value shape differs from its declaration. |
| `LEROBOT_FEATURE_NULLABILITY_MISMATCH` | error | A required feature contains null values. |
| `LEROBOT_DATA_ROW_COUNT_MISMATCH` | error | A data-shard row count differs from its prepared extent. |
| `LEROBOT_EPISODE_ROW_COUNT_MISMATCH` | error | Episode rows differ from the declared episode length. |
| `LEROBOT_FRAME_INDEX_DUPLICATE` | error | A frame index is duplicated within an episode. |
| `LEROBOT_FRAME_INDEX_NON_CONTIGUOUS` | error | Episode frame indexes contain a gap. |
| `LEROBOT_GLOBAL_INDEX_DUPLICATE` | error | A dataset-global row index is duplicated. |
| `LEROBOT_GLOBAL_INDEX_NON_CONTIGUOUS` | error | Dataset-global row indexes contain a gap. |
| `LEROBOT_NUMERIC_NON_FINITE` | error | A required numeric value is NaN or infinite. |
| `LEROBOT_TASK_REFERENCE_INVALID` | error | A frame or episode references an unknown task. |
| `LEROBOT_TIMESTAMP_NON_MONOTONIC` | error | Episode timestamps are not strictly monotonic. |
| `LEROBOT_TIMESTAMP_OFF_GRID` | error | A timestamp falls outside the declared FPS grid tolerance. |
| `LEROBOT_VIDEO_MISSING` | error | An episode's local video reference is missing. |
| `LEROBOT_VIDEO_COVERAGE_MISSING` | error | A declared episode camera has no readable media relation. |
| `LEROBOT_VIDEO_RESOLUTION_MISMATCH` | error | Observed resolution differs from the declared camera contract. |
| `LEROBOT_VIDEO_FPS_MISMATCH` | error | Observed FPS differs from the declared camera contract. |
| `LEROBOT_VIDEO_CHANNELS_MISMATCH` | error | Observed channel count differs from the declared camera contract. |
| `LEROBOT_VIDEO_RELATION_MISSING` | error | A LeRobot v3 episode lacks shard relation metadata for a declared video stream. |
| `LEROBOT_VIDEO_RELATION_INVALID` | error | A LeRobot v3 shard relation contains an invalid chunk or file index. |
| `LEROBOT_VIDEO_PATH_TEMPLATE_INVALID` | error | The declared v3 video path template is malformed or lacks required placeholders. |
| `LEROBOT_VIDEO_PATH_INVALID` | error | An episode video path is not a portable dataset-relative path. |
| `LEROBOT_VIDEO_SEGMENT_BOUNDS_INVALID` | error | Segment timestamps are missing, non-numeric, non-finite, negative, or unordered. |
| `LEROBOT_VIDEO_SEGMENT_OVERLAP` | error | Two episodes overlap within the same camera and shared video shard. |
| `LEROBOT_VIDEO_SEGMENT_OUT_OF_RANGE` | error | A segment extends beyond the referenced video duration by more than one frame. |
| `LEROBOT_VIDEOS_MISSING` | error | Video streams are declared but no local videos exist. |
| `LEROBOT_STATS_MISSING` | warning | Stored normalization statistics are missing. |
| `LEROBOT_STATS_FIELD_MISSING` | warning | A required normalization statistic field is missing. |
| `LEROBOT_STATS_SHAPE_MISMATCH` | error | Stored statistic shape differs from the feature shape. |
| `LEROBOT_STATS_COUNT_MISMATCH` | error | Stored statistic count differs from validated rows. |
| `LEROBOT_STATS_NON_FINITE` | error | Stored normalization statistics contain NaN or infinity. |
| `LEROBOT_STATS_VALUE_MISMATCH` | error | Stored statistics differ from full-data recomputation. |
| `VIDEO_UNREADABLE` | error | A video cannot be opened as a non-empty stream. |
| `VIDEO_INVALID_FPS` | error | FPS is zero or negative. |
| `VIDEO_INVALID_DURATION` | error | Duration is zero or negative. |
| `VIDEO_INVALID_DIMENSIONS` | error | Width or height is zero or negative. |
| `VIDEO_PREVIEW_DECODE_FAILED` | error | A requested deterministic decode probe fails; sample mode checks start, middle, and end. |
| `STREAM_INCONSISTENT_RESOLUTION` | warning | One camera stream contains multiple resolutions. |
| `STREAM_INCONSISTENT_FPS` | warning | One camera stream contains multiple FPS values. |
| `DUPLICATE_CONTENT` | warning | SHA-256 checking found duplicate file content. |

Messages may gain detail, but code and severity are the stable automation
contract for `0.0.x`.
