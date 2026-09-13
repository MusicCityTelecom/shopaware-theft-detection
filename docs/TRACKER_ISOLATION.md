# Camera tracker isolation

Implementation targets `ultralytics==8.4.150`; changing the pin requires rerunning
the adapter tests and reviewing the upstream tracker contract.

## Source investigation

Reviewed exact version sources:

- [track.py](https://github.com/ultralytics/ultralytics/blob/v8.4.150/ultralytics/trackers/track.py):
  `on_predict_start` returns early when `persist` and `predictor.trackers` exist.
  It creates one tracker per dataset batch index only in `dataset.mode == "stream"`.
  Other modes break after the first tracker. Postprocessing uses
  `trackers[i if is_stream else 0]`. With persistence enabled, source-path changes
  do not reset the tracker. Sequential unrelated NumPy camera frames therefore
  contaminate association history.
- [byte_tracker.py](https://github.com/ultralytics/ultralytics/blob/v8.4.150/ultralytics/trackers/byte_tracker.py)
  and [basetrack.py](https://github.com/ultralytics/ultralytics/blob/v8.4.150/ultralytics/trackers/basetrack.py):
  tracked/lost/removed pools, frame counters and Kalman state belong to tracker
  instances, but default ID allocation uses `BaseTrack._count`. Construction and
  reset reset that global counter. Independent tracker instances alone therefore
  do not provide independent ID allocation.
- [predictor.py](https://github.com/ultralytics/ultralytics/blob/v8.4.150/ultralytics/engine/predictor.py):
  `setup_model` calls `deepcopy(model)` before `AutoBackend`. Passing the same model
  into multiple ordinary predictors does not guarantee shared weight storage.
- [bot_sort.py](https://github.com/ultralytics/ultralytics/blob/v8.4.150/ultralytics/trackers/bot_sort.py):
  BoT-SORT adds camera motion compensation state and optional appearance/ReID
  encoding. These must also be camera-local; external ReID encoders may consume
  additional GPU memory. ByteTrack uses motion/IoU and confidence association,
  with no additional neural network or cross-camera identity matching.

## Selected architecture

One shared pose model performs `predict`, never `track`. Each camera owns a
`CameraTrackingContext` containing a ByteTrack instance, private ID allocator and
behavioral state. `CameraByteTracker` uses the pinned `track_class` extension
point to supply an STrack subclass whose `next_id` closes over a private counter.
It overrides the upstream global counter reset. No monkeypatching or switching
of global state is used. Detection-to-keypoint indices follow ByteTrack's final
source-index column, including filtered/reordered and empty results.

The scheduler serializes model inference. Tracking state resides in CPU memory;
camera count does not create additional YOLO predictors/weight copies. The
single predictor's own retained model copy still exists. Exact RAM, VRAM and
throughput remain unmeasured on target hardware; no GPU qualification is claimed.

The multi-stream API could isolate tracker pools if source indexes remained
stable. It was not selected because dynamic camera removal/reconnection would
couple ShopAware's lifecycle to that source-index contract and would still need
the ID allocator issue addressed.

## Lifecycle and tests

Capture reconnect increments a generation. The next processed frame resets only
that camera's tracker, person state, object cache and dwell state; resolution
changes do likewise. Deletion closes the context under its lock. Processing
holds that lock, so a stale camera reference cannot recreate state after close.
IDs persist within a generation; they are not global identities or identities
across reconnects. No face recognition is used.

Regression tests compare exact boxes, IDs and keypoints for camera A run alone
versus interleaved with B, including B resets. They test independent activation,
local ID allocation, low-confidence association, empty frames, stale cleanup,
keypoint indexing, behavior isolation and closed-context rejection. Ingest tests
use synthetic captures to verify reconnect, raw evidence delivery, stale reads,
redacted failures and interruptible shutdown.

Issue #3 remains open for its explicit real two-source acceptance criterion.
Synthetic tracker evidence fixes the software defect but does not establish
end-to-end camera/model quality. Tommy must still run two independent cameras,
reconnect/delete one during inference, inspect ID overlays and candidate evidence,
and measure latency/VRAM/channel capacity with the actual YOLO26 checkpoints.
