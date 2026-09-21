import os

import pytest
from ultralytics import YOLO


pytestmark = pytest.mark.skipif(
    os.getenv("SHOPAWARE_RUN_MODEL_TESTS") != "1",
    reason="set SHOPAWARE_RUN_MODEL_TESTS=1 to download/load YOLO26 checkpoints",
)


def test_yolo26_detection_model_loads():
    model = YOLO(os.getenv("SHOPAWARE_DETECTION_MODEL", "yolo26n.pt"))
    assert model is not None


def test_yolo26_pose_model_loads():
    model = YOLO(os.getenv("SHOPAWARE_POSE_MODEL", "yolo26n-pose.pt"))
    assert model is not None
