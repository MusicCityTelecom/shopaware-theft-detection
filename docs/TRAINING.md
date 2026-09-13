# Train from a selected camera

Open **Training** in the dashboard, or **Cameras → Train with this camera**.
This workflow collects labeled examples for fine-tuning the YOLO26 **object detector**.
It does not learn theft automatically from a feed, retrain from incident review
statuses, train pose keypoints, or learn a temporal activity model. An incident's
human review status is not a bounding-box annotation.

Start by tuning zones and the heuristic risk threshold. If the detector misses or
misidentifies visible objects, a representative labeled dataset can help. Training
can also reduce performance when examples are sparse, biased or incompletely labeled.

## 1. Collect and review

1. Select any configured camera. Enable it and wait for a live frame to capture.
   You can review earlier examples while the camera is disabled.
2. Create a named collection session for a recording period/scene. Assign it to
   **Training**, **Validation**, or **Test**. Sessions cannot change splits after
   creation. Use separate visits or time periods for each split; keep adjacent
   frames and the same event together. Do not recreate a session in another split
   using the same footage. Exact duplicate images from one camera are rejected;
   near duplicates and related scenes still require operator judgment.
3. Click **Capture current frame**. Only an explicit click collects one image.
   Capture different poses, distances, lighting, occlusion and ordinary activity,
   plus suitable background examples. Stale/disconnected feeds are rejected.
4. Select an object class and click two opposite corners around each visible
   object. Label every recognizable COCO object, including people, not just the
   bottle/item that motivated the capture. The editor supports all 80 baseline
   COCO classes. Remove and redraw incorrect boxes. Confirm the review checkbox
   and save. For background, explicitly confirm that no class-list objects exist.
5. Repeat across independent collection sessions. Unreviewed images are excluded
   from exports. Reopen reviewed examples to correct labels. Delete unsuitable
   examples or complete sessions using the confirmation controls.

Images have no detection overlays and are resized to at most 1280 pixels on their
longest side. Ignore zones apply to inference; they do **not** redact these captures.
All admins can view the examples. Media and downloads require authentication;
mutations require CSRF and a trusted origin. Image bytes, annotations and review
identity are stored in the application's SQLite database. They are not encrypted
by the camera-password key; protect the database, backups and downloaded copies.
No images or credentials are uploaded to a training service by this workflow.
Exports contain images, labels, opaque IDs and capture timestamps, never RTSP URLs,
camera passwords, usernames or reviewer names.

Training storage has a separate application-wide cap of **64 MiB of JPEG data,
1,000 examples and 300 sessions**. Each JPEG is at most 1 MiB. This is a bounded
pilot curation workspace, not an unlimited production dataset repository. Export
and delete examples to free logical capacity. SQLite normally reuses freed pages;
deletion does not necessarily shrink its file immediately. Incident retention does
not delete training examples. Deleting a camera deletes its sessions and examples,
while historical incident retention remains separate. Back up wanted exports first.

## 2. Export and check

Click **Download reviewed dataset**. Export requires at least one reviewed image
with boxes in each split. This minimum checks format, not data sufficiency or
quality. There is no universal sample count that proves a useful detector.
Collect enough independent examples to represent each intended object and operating
condition, and measure performance before deciding the dataset is adequate.

Extract the ZIP into `training-data/camera` inside the ShopAware checkout:

```text
training-data/camera/
  data.yaml
  manifest.json
  README.txt
  images/{train,val,test}/<example-id>.jpg
  labels/{train,val,test}/<example-id>.txt
```

From the checkout with its Python environment activated and requirements installed:

```sh
python -m tools.train_camera --data training-data/camera/data.yaml --check-only
```

This checks the manifest, file presence, class mapping, bounding-box geometry,
duplicate image content, and that collection sessions do not span splits. It does
not download weights or run training. It cannot judge label accuracy, near-duplicate
scenes, object coverage or dataset sufficiency. Keep the generated `data.yaml` and
manifest intact; the helper intentionally accepts ShopAware exports only.

## 3. Train explicitly

Run training on a separate machine, or stop the camera-analysis service first to
avoid competing for CPU/GPU/RAM. The dashboard never launches a background GPU job.

```sh
# CPU; may be slow
python -m tools.train_camera --data training-data/camera/data.yaml --device cpu --epochs 50 --batch 4

# NVIDIA GPU 0, only with a working PyTorch CUDA installation
python -m tools.train_camera --data training-data/camera/data.yaml --device 0 --epochs 50 --batch 4
```

The explicit training command may download `yolo26n.pt`. It uses the project's
local-only callback guard to disable optional experiment logging, telemetry and
Platform checkpoint uploads, even if the machine has existing cloud credentials.
Core training callbacks are retained; saved Ultralytics/account settings are not
modified. A pinned-source regression test checks the integration registration
path. Review this guard when upgrading Ultralytics. The command uses the project's
pinned `ultralytics==8.4.150`, starts from YOLO26n, and writes a unique new run under
`runs/shopaware`. It does not overwrite an existing run or the active checkpoint.
The original 80-class ordering is retained because the runtime's item-interaction
logic uses COCO numeric IDs. Do not substitute a one-class custom dataset, relabel
class 0 as theft, or install these detection weights as the pose model.

After training, the helper checks checkpoint task/class compatibility and evaluates
`best.pt` on the held-out test split. `shopaware-report.json` beside the run results
records test metrics, checkpoint path and device, with `activated: false` and
`quality_qualified: false`. Interrupted/failed training does not activate anything.
Outputs and camera datasets are excluded from Git and Docker build context.

The test set should be used for final evaluation. If repeatedly tuning from its
results, it has become development data: collect a fresh held-out test set.

## 4. Qualify and activate manually

Review per-class precision/recall, missed objects and false detections on independent
camera scenes. Compare against the baseline using the same test set and settings:

```sh
yolo detect val model=yolo26n.pt data=training-data/camera/data.yaml split=test device=cpu
```

Also check representative non-camera-specific scenes: fine-tuning on a single
camera can forget other classes or conditions. A good object-detection metric does
not establish theft-detection accuracy. Validate downstream candidate incidents,
false alarms per camera-hour, missed staged interactions and normal customer
activity. Keep original baseline results and checkpoints for rollback.

When qualified in the target environment:

1. Copy the trusted checkpoint to a path readable by the backend. For Docker,
   mount the directory read-only and use the container path in settings.
2. Set `SHOPAWARE_ENABLE_SPECIALIZED_MODEL=false` in the backend environment;
   otherwise an existing `shoplifting.pt` can override the detection setting.
3. In **Settings → Detection model**, enter that checkpoint's server-side path,
   save and restart the backend. Keep `yolo26n-pose.pt` as the pose model.
4. Verify authenticated health/model state and run the real camera qualification
   procedure in [DEPLOYMENT.md](DEPLOYMENT.md).
5. To roll back, set detection model to `yolo26n.pt`, save and restart.

Camera selection controls dataset collection only. **Model activation currently
affects every camera**, since inference shares one detector. Per-camera model
assignment and online retraining are not implemented. No trained ShopAware model
or hardware qualification is supplied by this code change.

References: [Ultralytics detection dataset format](https://docs.ultralytics.com/datasets/detect/),
[detection training and validation](https://docs.ultralytics.com/tasks/detect/).
Ultralytics code and model artifacts have separate licensing; upstream MIT attribution
does not relicense them. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
