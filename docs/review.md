# Local episode Review workbench

> Status checked 2026-10-02: implemented in the source checkout after tag
> `v0.0.3`, but not included in the published PyPI `0.0.3` wheel.

The workbench displays one local LeRobot v3 dataset in a browser. It reuses the
dataset discovery and metadata audit, then lets you browse episodes, camera
segments, sampled motion traces, and findings. It is a read-only inspection
surface; it does not annotate, delete, trim, repair, merge, train, or upload data.

## Install and start

Use Python 3.9–3.12 in a source checkout:

```bash
git clone https://github.com/openbotai/openbot-data.git
cd openbot-data
python -m pip install -e '.[lerobot]'
openbot-data review /path/to/lerobot_dataset
```

The `lerobot` extra installs PyArrow for episode metadata and motion traces; it
does not install the full LeRobot training package. Install `ffmpeg` separately
and ensure it is on `PATH` with the `libx264` encoder available for browser video
previews. The workbench can still show metadata, traces, and findings when
FFmpeg is absent.

The command binds to `127.0.0.1`, prints the local URL, and opens a browser by
default. The default port is `8766`:

```bash
openbot-data review /path/to/lerobot_dataset --port 8767 --no-open
openbot-data review /path/to/lerobot_dataset --port 0 --no-open
```

Port `0` asks the operating system for a free port. Open the printed URL and
stop the server with `Ctrl-C`. Opening `review.html` directly cannot load a
dataset.

## What the page shows

- Episode indexes, frame counts, tasks, search, and a filter for episodes with
  findings.
- Per-camera video segments with a shared playback timeline.
- Sampled `observation.state` and `action` traces when the relevant Parquet
  columns are readable.
- Episode findings, dataset-wide findings, and skipped audit rules with reasons.

The startup audit uses `integrity="metadata"`. Its counts reflect that audit;
zero errors or warnings does not establish frame-level integrity, training
readiness, task success, or motion quality. For a separate full audit and gate:

```bash
openbot-data audit /path/to/lerobot_dataset \
  --format lerobot --integrity full --out ./audit.json --fail-on error
openbot-data readiness /path/to/lerobot_dataset \
  --profile lerobot-core --integrity full --out ./readiness.json
```

Those outputs are not imported into the running workbench. Restart Review after
changing the dataset so that its discovery and audit match the new input.

## Limits and data handling

- Only local LeRobot v3 datasets are accepted. Video directories, LeRobot v2.1,
  Hub URLs, and robomimic/HDF5 files are outside the current Review scope.
- Each trace contains at most 1,500 sampled frames and the first 16 dimensions
  of each state/action value. These plots are not a full numeric validation.
- Each camera preview is limited to the first 180 seconds of its episode
  segment, scales to at most 640 pixels wide, and omits audio.
- FFmpeg writes previews into a temporary directory outside the dataset. The
  cache evicts above 256 MiB or 32 files; active HTTP streams may temporarily
  exceed those limits. Normal server shutdown removes the temporary previews.
- Source files are not modified. Review media and trace paths must be regular,
  dataset-confined files; symlinked files are rejected by those handlers.
- Findings on a shared data/video shard appear for every affected episode.
  Summary totals count each finding once.
- A missing or corrupt motion shard leaves video and audit findings available,
  with a trace error shown in place of the chart.

## Troubleshooting

| Symptom | Action |
|---|---|
| `No such command 'review'` | Use the current source checkout and its editable install; the published `0.0.3` wheel has no Review command. |
| Missing Parquet dependency | Install the source with `python -m pip install -e '.[lerobot]'`. |
| Video preview unavailable | Check that `ffmpeg` is on `PATH` and supports `libx264`; inspect video-related audit findings. |
| Port already in use | Choose another `--port`, or use `--port 0`. |
| Empty or failed motion traces | Check episode/frame columns, state/action columns, and shard findings. |
| Counts appear stale | Stop and restart Review after editing the dataset. |

Python/HTTP behavior is covered by `tests/test_review.py`; browser-state logic
is covered by `tests/review_ui.test.cjs`. FFmpeg and Node.js are needed to run
the respective optional checks.
