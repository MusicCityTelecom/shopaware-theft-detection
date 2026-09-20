"""Persisted configuration. Saved runtime changes take effect on process restart."""
from __future__ import annotations

import json
import os
from pydantic import BaseModel, Field

from shopaware.db import Database
from shopaware.security import SecretStore

ENV_FIELDS = {
    'detection_model': 'SHOPAWARE_DETECTION_MODEL', 'pose_model': 'SHOPAWARE_POSE_MODEL',
    'risk_threshold': 'SHOPAWARE_RISK_THRESHOLD', 'inference_fps': 'SHOPAWARE_INFERENCE_FPS',
    'pre_seconds': 'SHOPAWARE_PRE_EVENT_SECONDS', 'post_seconds': 'SHOPAWARE_POST_EVENT_SECONDS',
    'recording_fps': 'SHOPAWARE_RECORDING_FPS', 'retention_days': 'SHOPAWARE_RETENTION_DAYS',
    'max_storage_bytes': 'SHOPAWARE_MAX_STORAGE_BYTES', 'smtp_host': 'SMTP_HOST',
    'smtp_port': 'SMTP_PORT', 'smtp_username': 'SMTP_USERNAME', 'smtp_from': 'SMTP_FROM', 'smtp_to': 'SMTP_TO',
}


class SettingsInput(BaseModel):
    detection_model: str = Field(default='yolo26n.pt', min_length=1, max_length=1024)
    pose_model: str = Field(default='yolo26n-pose.pt', min_length=1, max_length=1024)
    risk_threshold: float = Field(default=65, gt=0, le=100)
    inference_fps: float = Field(default=5, ge=.1, le=60)
    pre_seconds: float = Field(default=15, ge=1, le=300)
    post_seconds: float = Field(default=30, ge=1, le=300)
    recording_fps: float = Field(default=6, ge=1, le=30)
    retention_days: float = Field(default=30, ge=1, le=3650)
    max_storage_bytes: int = Field(default=10*1024**3, ge=1024**2)
    smtp_host: str = Field(default='', max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = Field(default='', max_length=512)
    smtp_password: str | None = Field(default=None, max_length=1024)
    smtp_from: str = Field(default='', max_length=512)
    smtp_to: str = Field(default='', max_length=2048)


class SettingsStore:
    def __init__(self, database: Database, secrets: SecretStore):
        self.database, self.secrets = database, secrets

    def _saved(self) -> dict:
        conn = self.database.connect()
        try:
            row = conn.execute("SELECT value_json FROM settings WHERE key='runtime'").fetchone()
            return json.loads(row[0]) if row else {}
        finally:
            conn.close()

    def load_environment(self) -> None:
        saved = self._saved()
        for field, env in ENV_FIELDS.items():
            if field in saved:
                os.environ[env] = str(saved[field])
        if 'smtp_password_enc' in saved:
            os.environ['SMTP_PASSWORD'] = self.secrets.decrypt(saved['smtp_password_enc'])

    def public(self) -> dict:
        settings = SettingsInput.model_validate({f: os.environ[e] for f,e in ENV_FIELDS.items() if e in os.environ}).model_dump(exclude={'smtp_password'})
        saved = self._saved()
        settings.update({k:v for k,v in saved.items() if k in ENV_FIELDS})
        settings['has_smtp_password'] = bool(saved.get('smtp_password_enc') or os.getenv('SMTP_PASSWORD'))
        return settings

    def save(self, settings: SettingsInput) -> dict:
        saved = settings.model_dump(exclude={'smtp_password'})
        existing = self._saved()
        saved['smtp_password_enc'] = (self.secrets.encrypt(settings.smtp_password) if settings.smtp_password is not None
                                      else existing.get('smtp_password_enc', self.secrets.encrypt(os.getenv('SMTP_PASSWORD', ''))))
        conn = self.database.connect()
        try:
            conn.execute("INSERT INTO settings VALUES('runtime',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (json.dumps(saved),))
            conn.commit()
        finally:
            conn.close()
        return self.public()
