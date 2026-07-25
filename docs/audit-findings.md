# OpenBot Data Audit Findings

`openbot.dataset_audit.v1` reports evidence-backed findings instead of an
uncalibrated numeric quality score.

| Code | Severity | Meaning |
|---|---|---|
| `DATASET_NOT_FOUND` | error | The requested dataset directory does not exist. |
| `DATASET_INVALID_ARGUMENT` | error | Input format or checksum configuration is unsupported. |
| `DATASET_PATH_OUTSIDE_ROOT` | error | A media path or symlink resolves outside the dataset root. |
| `LEROBOT_INFO_MISSING` | error | `meta/info.json` is missing. |
| `LEROBOT_METADATA_INVALID` | error | LeRobot JSON metadata cannot be parsed as an object. |
| `LEROBOT_EPISODES_MISSING` | error | No JSONL or parquet episode metadata exists. |
| `LEROBOT_EPISODES_UNREADABLE` | error | Episode JSONL/parquet cannot be read. |
| `LEROBOT_DEPENDENCY_MISSING` | error | Parquet discovery needs the optional `lerobot` extra. |
| `LEROBOT_EPISODE_INVALID` | error | An episode JSONL record is malformed. |
| `LEROBOT_EPISODE_INDEX_INVALID` | error | An episode has no valid integer index. |
| `LEROBOT_EPISODE_COUNT_MISMATCH` | error | Declared and discovered episode counts differ. |
| `LEROBOT_VIDEO_MISSING` | error | An episode's local video reference is missing. |
| `LEROBOT_VIDEO_RELATION_MISSING` | error | A LeRobot v3 episode lacks shard relation metadata for a declared video stream. |
| `LEROBOT_VIDEOS_MISSING` | error | Video streams are declared but no local videos exist. |
| `VIDEO_UNREADABLE` | error | A video cannot be opened as a non-empty stream. |
| `VIDEO_INVALID_FPS` | error | FPS is zero or negative. |
| `VIDEO_INVALID_DURATION` | error | Duration is zero or negative. |
| `VIDEO_INVALID_DIMENSIONS` | error | Width or height is zero or negative. |
| `VIDEO_PREVIEW_DECODE_FAILED` | error | The first evidence frame cannot be decoded. |
| `STREAM_INCONSISTENT_RESOLUTION` | warning | One camera stream contains multiple resolutions. |
| `STREAM_INCONSISTENT_FPS` | warning | One camera stream contains multiple FPS values. |
| `DUPLICATE_CONTENT` | warning | SHA-256 checking found duplicate file content. |

Messages may gain detail, but code and severity are the stable automation
contract for `0.0.x`.
