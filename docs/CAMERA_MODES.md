# Camera analytics modes

An administrator can enable any combination of four modes on each camera under **Cameras → Enabled analytics modes**. Existing cameras migrate to **Shoplifting** only; no new collection begins until an administrator enables another mode. Regular users can view Analytics observations only for cameras assigned to them.

| Mode | Output | Current implementation and boundary |
| --- | --- | --- |
| Shoplifting | Reviewable incident, snapshot and pre/post-event clip | Existing pose, item, zone and temporal heuristic pipeline. It does not prove theft. |
| Vehicle break-in | Reviewable incident, snapshot and pre/post-event clip | Person-near-vehicle dwell plus repeated hand/entry-area interaction. The event is explicitly a `vehicle_break_in_candidate`, never a confirmed crime. |
| LPR | Vehicle snapshot with the plate outlined, OCR candidate, OCR confidence and estimated vehicle color | CPU OpenCV locator plus Tesseract. Text and color require human verification. Make/model fields remain **not classified** until a separately licensed and qualified classifier is added. |
| Face Capture | Up to five face crops under one anonymous camera-track section | Detects a face inside a YOLO pose track. It does not name, recognize, embed, compare, or match people across tracks, cameras, reconnects, or visits. |

## Configure a camera

1. Sign in as an administrator and open **Cameras**.
2. Select one or more checkboxes under **Enabled analytics modes**. At least one mode is required.
3. For Shoplifting, draw merchandise, checkout, exit, restricted and ignore zones.
4. For Vehicle break-in, draw a **Parking** zone around the relevant parking area. If no Parking zone exists, all detected vehicles in the frame are considered. Draw **Ignore** zones over public roads, windows, neighboring property, or irrelevant areas.
5. Use **Analytics** for LPR and Face Capture observations. Use **Incidents** for Shoplifting and Vehicle break-in candidates.
6. Stage consented daytime/nighttime tests, then verify snapshots, clips, plate text, camera grouping, false positives, CPU use and event latency.

Multiple modes share a camera's decoded frames and YOLO inference, but LPR and Face Capture add CPU work and stored images. On Server2, start with one camera and one added mode, keep `SHOPAWARE_INFERENCE_FPS=2`, and observe resource use before combining modes or adding cameras.

## Model compatibility

Vehicle detection for LPR and Vehicle break-in uses the standard COCO classes in `yolo26n.pt`. Keep `SHOPAWARE_ENABLE_SPECIALIZED_MODEL=false` when using those modes. A globally activated specialized Shoplifting detector may not contain vehicle classes, so ShopAware deliberately does not manufacture vehicle results from it.

The included LPR path is a conservative CPU baseline, not a production ALPR guarantee. It may miss plates that are small, blurred, angled, overexposed, obscured, or outside its locator's geometry. Do not use OCR text as the sole basis for enforcement. Make/model inference is not implemented in beta.4; showing “not classified” is intentional.

## Face privacy and retention

Face Capture is anonymous detection, not facial recognition. Its grouping key is an ephemeral pose-track ID scoped to one camera stream generation. Reconnects and tracker resets start new groups. The system stores crops in the same bounded media area and applies the configured global retention period/quota.

Before enabling it, establish a legitimate purpose, signs/notice where required, access rules, a short retention period, and a process for access/deletion requests. Laws may treat face images as personal or biometric data even without identity matching. Do not use this mode to infer identity, protected traits, emotion, intent, or criminality.

## Acceptance checklist

- Verify every enabled mode with representative consented footage from that exact camera and lighting.
- Confirm Ignore/Parking zones exclude roads, hotel rooms, neighboring property and other irrelevant space.
- Check plate characters against the original video; record error rates instead of assuming correctness.
- Confirm face groups split after reconnects and never display names or cross-visit matches.
- Review Vehicle break-in events as activity candidates and document false-positive causes such as owners loading vehicles, valets, maintenance and reflections.
- Confirm assigned users cannot retrieve observations or snapshots from another customer camera.
- Recheck storage growth, retention, backups, CPU, RAM, frame age and alert latency after each mode change.
