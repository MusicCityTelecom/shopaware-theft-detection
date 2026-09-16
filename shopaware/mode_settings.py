"""Validated per-camera analytics settings.

Defaults preserve the behavior shipped in v0.1.0-beta.4. These values tune
heuristics and collection cadence; they are not calibrated probabilities.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _StrictSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShopliftingModeSettings(_StrictSettings):
    risk_threshold: float = Field(default=65.0, gt=0, le=100)
    risk_window_seconds: float = Field(default=10.0, gt=0, le=300)
    candidate_cooldown_seconds: float = Field(default=60.0, gt=0, le=3600)
    loitering_seconds: float = Field(default=12.0, gt=0, le=3600)
    concealment_window_seconds: float = Field(default=3.0, gt=0, le=30)


class VehicleBreakInModeSettings(_StrictSettings):
    risk_threshold: float = Field(default=65.0, gt=0, le=100)
    dwell_seconds: float = Field(default=12.0, gt=0, le=3600)
    required_access_interactions: int = Field(default=3, ge=1, le=50)
    access_interval_seconds: float = Field(default=1.5, gt=0, le=60)
    candidate_cooldown_seconds: float = Field(default=90.0, gt=0, le=3600)


class LprModeSettings(_StrictSettings):
    observation_cooldown_seconds: float = Field(default=60.0, ge=0, le=86400)
    min_ocr_confidence: float = Field(default=0.20, ge=0, le=1)
    min_plate_chars: int = Field(default=4, ge=1, le=16)
    max_plate_chars: int = Field(default=10, ge=1, le=16)


class FaceCaptureModeSettings(_StrictSettings):
    capture_cooldown_seconds: float = Field(default=8.0, ge=0, le=3600)
    max_images_per_track: int = Field(default=5, ge=1, le=100)
    min_quality: float = Field(default=0.0, ge=0, le=1)


class CameraModeSettingsInput(_StrictSettings):
    shoplifting: ShopliftingModeSettings = Field(default_factory=ShopliftingModeSettings)
    vehicle_break_in: VehicleBreakInModeSettings = Field(default_factory=VehicleBreakInModeSettings)
    lpr: LprModeSettings = Field(default_factory=LprModeSettings)
    face_capture: FaceCaptureModeSettings = Field(default_factory=FaceCaptureModeSettings)


def parse_mode_settings(value: str | dict[str, Any] | None) -> dict[str, Any]:
    """Return a complete validated settings dictionary with defaults filled in."""
    if value in (None, ""):
        raw: dict[str, Any] = {}
    elif isinstance(value, str):
        loaded = json.loads(value)
        if not isinstance(loaded, dict):
            raise ValueError("Camera mode settings must be a JSON object")
        raw = loaded
    elif isinstance(value, dict):
        raw = value
    else:
        raise ValueError("Unsupported camera mode settings value")

    settings = CameraModeSettingsInput.model_validate(raw)
    if settings.lpr.max_plate_chars < settings.lpr.min_plate_chars:
        raise ValueError("LPR max_plate_chars must be greater than or equal to min_plate_chars")
    return settings.model_dump()
