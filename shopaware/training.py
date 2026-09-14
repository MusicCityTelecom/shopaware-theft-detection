"""Operator-reviewed camera examples; no automatic learning or model activation."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import uuid
import zipfile
from contextlib import closing
from typing import Literal

import cv2
from pydantic import BaseModel, Field, model_validator

from shopaware.db import Database, utc_now_iso

# COCO detection class order: retain the baseline runtime's numeric class contract.
COCO_NAMES = ('person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,traffic light,'
              'fire hydrant,stop sign,parking meter,bench,bird,cat,dog,horse,sheep,cow,'
              'elephant,bear,zebra,giraffe,backpack,umbrella,handbag,tie,suitcase,frisbee,'
              'skis,snowboard,sports ball,kite,baseball bat,baseball glove,skateboard,surfboard,'
              'tennis racket,bottle,wine glass,cup,fork,knife,spoon,bowl,banana,apple,sandwich,'
              'orange,broccoli,carrot,hot dog,pizza,donut,cake,chair,couch,potted plant,bed,'
              'dining table,toilet,tv,laptop,mouse,remote,keyboard,cell phone,microwave,oven,'
              'toaster,sink,refrigerator,book,clock,vase,scissors,teddy bear,hair drier,toothbrush').split(',')
MAX_BYTES = 64 * 1024 * 1024
MAX_SAMPLES = 1000
MAX_SESSIONS = 300


class SessionInput(BaseModel):
    camera_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=80)
    split: Literal['train', 'val', 'test']


class Box(BaseModel):
    class_id: int = Field(ge=0, le=79, strict=True)
    x1: float = Field(ge=0, le=1, allow_inf_nan=False)
    y1: float = Field(ge=0, le=1, allow_inf_nan=False)
    x2: float = Field(ge=0, le=1, allow_inf_nan=False)
    y2: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode='after')
    def area(self):
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError('Box must have positive width and height')
        return self


class AnnotationInput(BaseModel):
    boxes: list[Box] = Field(max_length=200)
    reviewed: Literal[True]


class TrainingStore:
    def __init__(self, database: Database):
        self.database = database

    def create_session(self, payload: SessionInput) -> dict:
        with closing(self.database.connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM cameras WHERE id=?', (payload.camera_id,)).fetchone():
                raise LookupError('Camera not found')
            if conn.execute('SELECT COUNT(*) FROM training_sessions').fetchone()[0] >= MAX_SESSIONS:
                raise ValueError('Session limit reached. Delete an old collection session first.')
            session = dict(id=uuid.uuid4().hex, **payload.model_dump(), created_at=utc_now_iso())
            conn.execute('INSERT INTO training_sessions VALUES(:id,:camera_id,:name,:split,:created_at)', session)
            return session

    def session(self, session_id: str) -> dict:
        with closing(self.database.connect()) as conn:
            row = conn.execute('SELECT * FROM training_sessions WHERE id=?', (session_id,)).fetchone()
            if not row:
                raise LookupError('Collection session not found')
            return dict(row)

    def overview(self, camera_id: str) -> dict:
        with closing(self.database.connect()) as conn:
            sessions = [dict(r) for r in conn.execute('SELECT * FROM training_sessions WHERE camera_id=? ORDER BY created_at DESC', (camera_id,))]
            samples = []
            for row in conn.execute('SELECT p.id,p.session_id,p.captured_at,p.width,p.height,p.boxes_json,p.reviewed,p.reviewer,s.split FROM training_samples p JOIN training_sessions s ON s.id=p.session_id WHERE p.camera_id=? ORDER BY p.captured_at DESC', (camera_id,)):
                item = dict(row)
                item['boxes'] = json.loads(item.pop('boxes_json'))
                samples.append(item)
            used = conn.execute('SELECT COALESCE(SUM(length(jpeg)),0) FROM training_samples').fetchone()[0]
        return dict(sessions=sessions, samples=samples, used_bytes=used, max_bytes=MAX_BYTES, classes=COCO_NAMES)

    def capture(self, session_id: str, frame, captured_at: float) -> dict:
        session = self.session(session_id)
        height, width = frame.shape[:2]
        scale = min(1, 1280 / max(height, width))
        if scale < 1:
            frame = cv2.resize(frame, (round(width * scale), round(height * scale)))
        ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ValueError('Unable to encode camera image')
        jpeg = encoded.tobytes()
        if len(jpeg) > 1024 * 1024:
            raise ValueError('Image exceeds the 1 MiB capture limit')
        sample_id = uuid.uuid4().hex
        with closing(self.database.connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            count, used = conn.execute('SELECT COUNT(*),COALESCE(SUM(length(jpeg)),0) FROM training_samples').fetchone()
            if count >= MAX_SAMPLES or used + len(jpeg) > MAX_BYTES:
                raise ValueError('Training storage limit reached. Export and delete old examples first.')
            try:
                conn.execute('INSERT INTO training_samples(id,session_id,camera_id,captured_at,jpeg,digest,width,height) VALUES(?,?,?,?,?,?,?,?)',
                             (sample_id, session_id, session['camera_id'], captured_at, jpeg, hashlib.sha256(jpeg).hexdigest(), frame.shape[1], frame.shape[0]))
            except sqlite3.IntegrityError as exc:
                raise ValueError('Identical image already captured, or camera/session was removed. Refresh and try a different scene.') from exc
        return dict(id=sample_id)

    def image(self, sample_id: str) -> bytes:
        with closing(self.database.connect()) as conn:
            row = conn.execute('SELECT jpeg FROM training_samples WHERE id=?', (sample_id,)).fetchone()
            if not row:
                raise LookupError('Example not found')
            return row[0]

    def annotate(self, sample_id: str, payload: AnnotationInput, reviewer: str) -> None:
        with closing(self.database.connect()) as conn, conn:
            result = conn.execute('UPDATE training_samples SET boxes_json=?,reviewed=1,reviewer=?,reviewed_at=? WHERE id=?',
                                 (json.dumps([b.model_dump() for b in payload.boxes]), reviewer, utc_now_iso(), sample_id))
            if not result.rowcount:
                raise LookupError('Example not found')

    def delete(self, item_id: str, *, session: bool = False) -> None:
        table = 'training_sessions' if session else 'training_samples'
        with closing(self.database.connect()) as conn, conn:
            if not conn.execute(f'DELETE FROM {table} WHERE id=?', (item_id,)).rowcount:
                raise LookupError('Collection session or example not found')

    def export(self, camera_id: str) -> bytes:
        with closing(self.database.connect()) as conn:
            rows = conn.execute('SELECT p.*,s.split FROM training_samples p JOIN training_sessions s ON p.session_id=s.id WHERE p.camera_id=? AND p.reviewed=1 ORDER BY p.id', (camera_id,)).fetchall()
        if any(not any(r['split'] == split and json.loads(r['boxes_json']) for r in rows) for split in ('train', 'val', 'test')):
            raise ValueError('Review at least one labeled example in each split: training, validation and test. Use separate recording sessions for each.')
        manifest = dict(format_version=1, task='detect', camera_id=camera_id, samples=[])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('data.yaml', json.dumps(dict(train='images/train', val='images/val', test='images/test', names=COCO_NAMES), indent=2))
            for row in rows:
                stem = f"{row['split']}/{row['id']}"
                boxes = [Box.model_validate(b) for b in json.loads(row['boxes_json'])]
                labels = ''.join(f'{b.class_id} {(b.x1+b.x2)/2:.8f} {(b.y1+b.y2)/2:.8f} {b.x2-b.x1:.8f} {b.y2-b.y1:.8f}\n' for b in boxes)
                archive.writestr(f'images/{stem}.jpg', row['jpeg'])
                archive.writestr(f'labels/{stem}.txt', labels)
                manifest['samples'].append(dict(id=row['id'], session_id=row['session_id'], split=row['split'], captured_at=row['captured_at']))
            archive.writestr('manifest.json', json.dumps(manifest, indent=2))
            archive.writestr('README.txt', 'ShopAware camera object-detection examples. No trained model is included.\n'
                'Keep this archive private; images contain camera footage. No camera credentials are included.\n'
                'Review ALL visible COCO objects; an empty label file explicitly means background.\n'
                'Train/val/test must come from different scenes/times, not adjacent frames.\n'
                'The export minimum is a format check, not evidence of sufficient training data.\n'
                'Use docs/TRAINING.md and python -m tools.train_camera --data <extracted>/data.yaml --check-only\n'
                'from the ShopAware checkout. Training is separate from the running camera service.\n')
        return buffer.getvalue()
