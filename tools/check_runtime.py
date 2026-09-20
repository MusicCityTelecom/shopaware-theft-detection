"""Read-only install checks plus disposable H.264 encoding and CPU model inference.

Run inside the backend container. Does not create accounts, cameras or incidents.
May download the standard YOLO26 checkpoints into the persistent model directory.
"""
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import torch
import ultralytics
from ultralytics import YOLO

from shopaware import __version__
from shopaware.media import media_writer
from shopaware.models import model_path
from shopaware.security import SecretStore


def main():
    database = Path(os.environ['SHOPAWARE_DB_PATH'])
    key = Path(os.environ['SHOPAWARE_KEY_FILE'])
    if not database.is_file() or not key.is_file():
        raise SystemExit('Database and encryption key must exist; start the backend first.')
    # Reading the saved key must not silently replace or regenerate it.
    SecretStore(key)
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        schema = conn.execute('PRAGMA user_version').fetchone()[0]
    encoder = media_writer()
    assert encoder.codec == 'h264', 'Install FFmpeg and use SHOPAWARE_MEDIA_WRITER=ffmpeg'
    with tempfile.TemporaryDirectory(prefix='shopaware-check-') as directory:
        image = np.zeros((64, 96, 3), np.uint8)
        ok, jpeg = cv2.imencode('.jpg', image)
        assert ok
        encoder.write(Path(directory) / 'check.mp4', [SimpleNamespace(timestamp=0, jpeg=jpeg.tobytes())], 2)
    image_path = Path(ultralytics.__file__).parent / 'assets' / 'bus.jpg'
    image = cv2.imread(str(image_path))
    assert image is not None
    models = []
    for checkpoint in ['yolo26n.pt', 'yolo26n-pose.pt']:
        result = YOLO(model_path(checkpoint)).predict(image, device='cpu', verbose=False)[0]
        assert len(result.boxes) > 0
        if 'pose' in checkpoint:
            assert result.keypoints.data.shape[1:] == (17, 3)
        models.append(checkpoint)
    print(json.dumps(dict(version=__version__, schema=schema, codec=encoder.codec,
                          models=models, cpu_inference='passed', cuda_available=torch.cuda.is_available())))


if __name__ == '__main__':
    main()
