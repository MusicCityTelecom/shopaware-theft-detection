# Third-Party Notices

## Theft-Detection upstream baseline

Portions of ShopAware are derived from or informed by:

- Project: `vahapogut/Theft-Detection`
- Reviewed revision: `fdba673494878d7c8bed7b324e071a209c158dff`
- Copyright (c) 2026 Abdulvahap Öğüt
- License: MIT

The upstream MIT license permits use, modification, merging, publication, distribution, sublicensing and sale, provided the copyright and permission notice are retained in copies or substantial portions of the software.

The upstream project is used as an implementation baseline for its FastAPI/Next.js multi-camera pipeline, ROI workflow, pose/tracking concepts, and concealment heuristics. ShopAware materially changes credential handling, event semantics, model generation, incident persistence, and deployment architecture.

## Ultralytics

ShopAware uses the `ultralytics` Python package and supports Ultralytics YOLO model checkpoints. Ultralytics software and model licensing is separate from the upstream MIT application license and from ShopAware's own source licensing. Review the current Ultralytics license terms before deployment or redistribution.

Standard model weights are intentionally not committed to this repository. They are resolved by the Ultralytics runtime or supplied by the operator.
