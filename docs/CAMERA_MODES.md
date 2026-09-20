# Camera analytics modes

An administrator can enable any combination of four modes on each camera under **Cameras → Enabled analytics modes**. Existing cameras migrate to **Shoplifting** only; no new collection begins until an administrator enables another mode. Regular users can view Analytics observations only for cameras assigned to them.

| Mode | Output | Current implementation and boundary |
| --- | --- | --- |
| Shoplifting | Reviewable incident, snapshot and pre/post-event clip | Existing pose, item, zone and temporal heuristic pipeline. It does not prove theft. |
| Vehicle break-in | Reviewable incident, snapshot and pre/post-event clip | Person-near-vehicle dwell plus repeated hand/entry-area interaction. The event is explicitly a `vehicle_break_in_candidate`, never a confirmed crime. |
| LPR | Vehicle snapshot with the plate outlined, OCR candidate, OCR confidence and estimated vehicle color | CPU OpenCV locator plus Tesseract. Text and color require human verification. Make/model fields remain **not classified** until a separately licensed and qualified classifier is added. |
| Face Capture | Multiple face crops under one anonymous continuous camera-track section | Detects a face inside a YOLO pose track. It does not name, recognize, embed, compare, or match people across tracks, cameras, reconnects, or visits. |

## Configure a camera

1. Sign in as an administrator and open **Cameras**.
2. Select one or more checkboxes under **Enabled analytics modes**. At least one mode is required.
3. For Shoplifting, draw merchandise, checkout, exit, restricted and ignore zones.
4. For Vehicle break-in, draw a **Parking** zone around the relevant parking area. If no Parking zone exists, all detected vehicles in the frame are considered. Draw **Ignore** zones over public roads, windows, neighboring property, or irrelevant areas.
5. Open **Mode Settings** to tune the enabled modes for that exact camera. Existing cameras initially use the beta.4-equivalent defaults listed below, while any previously customized beta.4 global Shoplifting risk/loitering values are preserved during migration.
6. Use **Analytics** for LPR and Face Capture observations. Use **Incidents** for Shoplifting and Vehicle break-in candidates.
7. Stage consented daytime/nighttime tests, then verify snapshots, clips, plate text, camera grouping, false positives, CPU use and event latency.

Multiple modes share a camera's decoded frames and YOLO inference, but LPR and Face Capture add CPU work and stored images. On Server2, start with one camera and one added mode, keep `SHOPAWARE_INFERENCE_FPS=2`, and observe resource use before combining modes or adding cameras.

## Per-camera mode settings

Beta.6 persists a complete settings object independently for every camera. Saving settings applies them without a service restart. They are reloaded after process/container restart and re-applied when a camera stream reconnects or starts a new stream generation.

The default values preserve beta.4 behavior:

| Mode | Setting | Default | Meaning |
| --- | --- | ---: | --- |
| Shoplifting | Risk threshold | 65 points | Heuristic score required before creating a review candidate. |
| Shoplifting | Signal window | 10 s | How long recent behavior signals remain active together. |
| Shoplifting | Candidate cooldown | 60 s | Suppresses duplicate candidates for the same tracked person. |
| Shoplifting | Loitering threshold | 12 s | Dwell time before excessive dwell becomes a signal. |
| Vehicle break-in | Risk threshold | 65 points | Heuristic score required before creating a review candidate. |
| Vehicle break-in | Near-vehicle dwell | 12 s | Proximity time before the loitering signal activates. |
| Vehicle break-in | Required access interactions | 3 | Separated hand-near-entry interactions before repeated access activates. |
| Vehicle break-in | Access interaction interval | 1.5 s | Minimum spacing between counted access interactions. |
| Vehicle break-in | Candidate cooldown | 90 s | Suppresses duplicate person/vehicle candidates. |
| LPR | Same-plate cooldown | 60 s | Suppresses repeated observations of the same OCR candidate. |
| LPR | Minimum OCR confidence | 20% | Rejects OCR candidates below this Tesseract confidence. |
| LPR | Plate length | 4–10 chars | Rejects OCR strings outside this range. |
| Face Capture | Capture cooldown | 8 s | Minimum interval between stored crops for one continuous track. |
| Face Capture | Max images per track | 5 | Storage cap for one continuous camera track. |
| Face Capture | Minimum image quality | 0% | Composite crop-size/sharpness gate. Raise gradually if saved crops are too blurry. |

These values are tuning controls, not calibrated probabilities. Lowering thresholds or cooldowns can sharply increase false positives, CPU work, alerts and storage use. Change one camera at a time and record the effect before applying similar settings elsewhere.

## Model compatibility

Vehicle detection for LPR and Vehicle break-in uses the standard COCO classes in `yolo26n.pt`. Keep `SHOPAWARE_ENABLE_SPECIALIZED_MODEL=false` when using those modes. A globally activated specialized Shoplifting detector may not contain vehicle classes, so ShopAware deliberately does not manufacture vehicle results from it.

The included LPR path is a conservative CPU baseline, not a production ALPR guarantee. It may miss plates that are small, blurred, angled, overexposed, obscured, or outside its locator's geometry. Do not use OCR text as the sole basis for enforcement. Make/model inference is not implemented in beta.7; showing “not classified” is intentional.

## Face privacy and retention

Face Capture is anonymous detection, not facial recognition. Its grouping key is an ephemeral pose-track ID scoped to one camera stream generation. Reconnects and tracker resets start new groups. The system stores crops in the same bounded media area and applies the configured global retention period/quota.

Before enabling it, establish a legitimate purpose, signs/notice where required, access rules, a short retention period, and a process for access/deletion requests. Laws may treat face images as personal or biometric data even without identity matching. Do not use this mode to infer identity, protected traits, emotion, intent, or criminality.

## Acceptance checklist

- Verify every enabled mode with representative consented footage from that exact camera and lighting.
- Confirm Ignore/Parking zones exclude roads, hotel rooms, neighboring property and other irrelevant space.
- Check plate characters against the original video; record error rates instead of assuming correctness.
- Confirm face groups split after reconnects and never display names or cross-visit matches.
- Review Vehicle break-in events as activity candidates and document false-positive causes such as owners loading vehicles, valets, maintenance and reflections.
- Confirm assigned users cannot retrieve observations or snapshots from another customer camera and cannot access Mode Settings.
- Recheck storage growth, retention, backups, CPU, RAM, frame age and alert latency after each mode or tuning change.
