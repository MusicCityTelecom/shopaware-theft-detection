"""Bounded camera analytics helpers for optional per-camera modes.

These helpers produce observations and review candidates. They do not identify a
person and they do not treat an OCR result or behavior score as a verified fact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np


CAMERA_MODES = ("shoplifting", "vehicle_break_in", "lpr", "face_capture")
DEFAULT_CAMERA_MODES = ("shoplifting",)
VEHICLE_CLASS_IDS = {2, 3, 5, 7}  # car, motorcycle, bus, truck in COCO


def normalize_modes(values: Iterable[str]) -> list[str]:
    modes = list(dict.fromkeys(values))
    unknown = sorted(set(modes) - set(CAMERA_MODES))
    if unknown:
        raise ValueError(f"Unsupported camera mode: {', '.join(unknown)}")
    if not modes:
        raise ValueError("Select at least one camera mode")
    return [mode for mode in CAMERA_MODES if mode in modes]


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    overlap = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = max(1.0, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap)
    return float(overlap / union)


def estimate_vehicle_color(crop: np.ndarray) -> tuple[str, float]:
    if crop.size == 0:
        return "unknown", 0.0
    h, w = crop.shape[:2]
    sample = crop[max(0, h // 5):max(1, 4 * h // 5), max(0, w // 5):max(1, 4 * w // 5)]
    hsv = cv2.cvtColor(sample, cv2.COLOR_BGR2HSV)
    pixels = hsv.reshape(-1, 3)
    if not len(pixels):
        return "unknown", 0.0
    med_h, med_s, med_v = np.median(pixels, axis=0)
    if med_v < 45:
        return "black", 0.65
    if med_s < 28 and med_v > 190:
        return "white", 0.65
    if med_s < 38:
        return ("silver" if med_v > 115 else "gray"), 0.55
    if med_h < 8 or med_h >= 170:
        return "red", 0.55
    if med_h < 22:
        return "orange/brown", 0.45
    if med_h < 38:
        return "yellow", 0.5
    if med_h < 85:
        return "green", 0.5
    if med_h < 135:
        return "blue", 0.55
    return "purple", 0.45


@dataclass
class PlateObservation:
    plate: str
    confidence: float
    crop: np.ndarray
    vehicle_color: str
    color_confidence: float


class PlateReader:
    """Conservative CPU plate locator/OCR. Empty results are preferred to guesses."""

    def __init__(
        self,
        cooldown_seconds: float = 60.0,
        min_ocr_confidence: float = 0.20,
        min_plate_chars: int = 4,
        max_plate_chars: int = 10,
    ) -> None:
        if cooldown_seconds < 0 or not 0 <= min_ocr_confidence <= 1:
            raise ValueError("Invalid LPR settings")
        if not 1 <= min_plate_chars <= max_plate_chars <= 16:
            raise ValueError("Invalid LPR plate length settings")
        cascade = Path(cv2.data.haarcascades) / "haarcascade_russian_plate_number.xml"
        self.locator = cv2.CascadeClassifier(str(cascade))
        self.cooldown_seconds = cooldown_seconds
        self.min_ocr_confidence = min_ocr_confidence
        self.min_plate_chars = min_plate_chars
        self.max_plate_chars = max_plate_chars
        self.last_seen: dict[str, float] = {}

    def _ocr(self, crop: np.ndarray) -> tuple[str, float]:
        try:
            import pytesseract
            from pytesseract import Output
        except ImportError:
            return "", 0.0
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
        gray = cv2.bilateralFilter(gray, 7, 50, 50)
        data = pytesseract.image_to_data(
            gray,
            config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            output_type=Output.DICT,
        )
        candidates = []
        for text, confidence in zip(data.get("text", []), data.get("conf", [])):
            value = re.sub(r"[^A-Z0-9]", "", str(text).upper())
            try:
                score = float(confidence) / 100.0
            except (TypeError, ValueError):
                score = -1.0
            if self.min_plate_chars <= len(value) <= self.max_plate_chars and score >= self.min_ocr_confidence:
                candidates.append((value, score))
        return max(candidates, key=lambda item: item[1], default=("", 0.0))

    def observe(self, frame: np.ndarray, vehicle_boxes: list[np.ndarray], now: float) -> list[PlateObservation]:
        results: list[PlateObservation] = []
        for vehicle in vehicle_boxes:
            x1, y1, x2, y2 = [int(v) for v in vehicle[:4]]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
            vehicle_crop = frame[y1:y2, x1:x2]
            if vehicle_crop.shape[0] < 50 or vehicle_crop.shape[1] < 80:
                continue
            color, color_confidence = estimate_vehicle_color(vehicle_crop)
            gray = cv2.cvtColor(vehicle_crop, cv2.COLOR_BGR2GRAY)
            plates = self.locator.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(50, 14))
            for px, py, pw, ph in plates:
                crop = vehicle_crop[py:py + ph, px:px + pw]
                plate, confidence = self._ocr(crop)
                if not plate or now - self.last_seen.get(plate, -1e9) < self.cooldown_seconds:
                    continue
                self.last_seen[plate] = now
                snapshot = vehicle_crop.copy()
                cv2.rectangle(snapshot, (px, py), (px + pw, py + ph), (0, 220, 0), 2)
                results.append(PlateObservation(plate, confidence, snapshot, color, color_confidence))
        self.last_seen = {plate: at for plate, at in self.last_seen.items() if now - at < 3600}
        return results


@dataclass
class FaceObservation:
    subject_key: str
    confidence: float
    crop: np.ndarray
    quality: float


class FaceCapture:
    """Face capture grouped only by continuous pose track; no biometric identity matching."""

    def __init__(self, cooldown_seconds: float = 8.0, max_per_track: int = 5, min_quality: float = 0.0) -> None:
        if cooldown_seconds < 0 or max_per_track < 1 or not 0 <= min_quality <= 1:
            raise ValueError("Invalid face capture settings")
        cascade = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        self.detector = cv2.CascadeClassifier(str(cascade))
        self.cooldown_seconds = cooldown_seconds
        self.max_per_track = max_per_track
        self.min_quality = min_quality
        self.state: dict[str, tuple[float, int, float]] = {}

    def observe(self, frame: np.ndarray, people: list[tuple[int, np.ndarray]], generation: int,
                now: float) -> list[FaceObservation]:
        found: list[FaceObservation] = []
        for track_id, person in people:
            x1, y1, x2, y2 = [int(v) for v in person[:4]]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
            upper = frame[y1:max(y1 + 1, min(y2, y1 + int((y2 - y1) * 0.48))), x1:x2]
            if upper.shape[0] < 40 or upper.shape[1] < 30:
                continue
            faces = self.detector.detectMultiScale(cv2.cvtColor(upper, cv2.COLOR_BGR2GRAY),
                                                   scaleFactor=1.08, minNeighbors=5, minSize=(36, 36))
            if not len(faces):
                continue
            fx, fy, fw, fh = max(faces, key=lambda box: box[2] * box[3])
            crop = upper[fy:fy + fh, fx:fx + fw]
            blur = float(cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
            quality = min(1.0, (fw * fh) / 18000.0) * min(1.0, blur / 180.0)
            if quality < self.min_quality:
                continue
            subject = f"track-{generation}-{track_id}"
            last, count, best = self.state.get(subject, (-1e9, 0, 0.0))
            if count >= self.max_per_track or now - last < self.cooldown_seconds:
                continue
            if count and quality < max(self.min_quality, 0.20, best * 0.70):
                continue
            self.state[subject] = (now, count + 1, max(best, quality))
            found.append(FaceObservation(subject, min(0.99, 0.5 + quality / 2), crop.copy(), quality))
        self.state = {key: value for key, value in self.state.items() if now - value[0] < 600}
        return found


@dataclass
class VehicleTrack:
    box: np.ndarray
    first_seen: float
    last_seen: float


@dataclass
class InteractionTrack:
    first_near: float
    last_seen: float
    reach_count: int = 0
    last_reach: float = -1e9
    last_candidate: float = -1e9


class VehicleBreakInDetector:
    """Explainable person/vehicle interaction candidates; never proof of a crime."""

    def __init__(
        self,
        quiet_seconds: float = 90.0,
        risk_threshold: float = 65.0,
        dwell_seconds: float = 12.0,
        required_access_interactions: int = 3,
        access_interval_seconds: float = 1.5,
    ) -> None:
        if quiet_seconds <= 0 or not 0 < risk_threshold <= 100 or dwell_seconds <= 0:
            raise ValueError("Invalid vehicle break-in settings")
        if required_access_interactions < 1 or access_interval_seconds <= 0:
            raise ValueError("Invalid vehicle break-in interaction settings")
        self.vehicles: dict[int, VehicleTrack] = {}
        self.interactions: dict[tuple[int, int], InteractionTrack] = {}
        self.next_vehicle_id = 1
        self.quiet_seconds = quiet_seconds
        self.risk_threshold = risk_threshold
        self.dwell_seconds = dwell_seconds
        self.required_access_interactions = required_access_interactions
        self.access_interval_seconds = access_interval_seconds

    def _update_vehicles(self, boxes: list[np.ndarray], now: float) -> dict[int, VehicleTrack]:
        unmatched = set(self.vehicles)
        updated: dict[int, VehicleTrack] = {}
        for box in boxes:
            match = max(unmatched, key=lambda key: _iou(self.vehicles[key].box, box), default=None)
            if match is not None and _iou(self.vehicles[match].box, box) >= 0.35:
                track = self.vehicles[match]
                unmatched.remove(match)
                updated[match] = VehicleTrack(np.asarray(box[:4]), track.first_seen, now)
            else:
                key = self.next_vehicle_id
                self.next_vehicle_id += 1
                updated[key] = VehicleTrack(np.asarray(box[:4]), now, now)
        self.vehicles = updated
        return updated

    def observe(self, people: list[tuple[int, np.ndarray, np.ndarray]], vehicles: list[np.ndarray],
                now: float) -> list[dict]:
        candidates = []
        tracked = self._update_vehicles(vehicles, now)
        active: set[tuple[int, int]] = set()
        for person_id, person_box, keypoints in people:
            px = float((person_box[0] + person_box[2]) / 2)
            py = float((person_box[1] + person_box[3]) / 2)
            for vehicle_id, vehicle in tracked.items():
                x1, y1, x2, y2 = vehicle.box
                margin_x, margin_y = max(25.0, (x2 - x1) * .28), max(20.0, (y2 - y1) * .25)
                if not (x1 - margin_x <= px <= x2 + margin_x and y1 - margin_y <= py <= y2 + margin_y):
                    continue
                key = (person_id, vehicle_id)
                active.add(key)
                state = self.interactions.setdefault(key, InteractionTrack(now, now))
                state.last_seen = now
                wrist_near = False
                for wrist in keypoints[9:11] if len(keypoints) >= 11 else []:
                    if wrist[0] > 0 and wrist[1] > 0 and x1 - 12 <= wrist[0] <= x2 + 12 and y1 - 12 <= wrist[1] <= y2 + 12:
                        wrist_near = True
                # Count separated access interactions, not every processed frame.
                if wrist_near and now - state.last_reach >= self.access_interval_seconds:
                    state.reach_count += 1
                    state.last_reach = now
                dwell = now - state.first_near
                signals = ["person_near_vehicle"]
                score = 20
                if dwell >= self.dwell_seconds:
                    signals.append("vehicle_loitering")
                    score += 20
                if state.reach_count >= self.required_access_interactions:
                    signals.append("repeated_vehicle_access")
                    score += 30
                if wrist_near:
                    signals.append("hand_near_vehicle_entry")
                    score += 20
                if score >= self.risk_threshold and now - state.last_candidate >= self.quiet_seconds:
                    state.last_candidate = now
                    candidates.append({
                        "event_type": "vehicle_break_in_candidate",
                        "risk_score": min(1.0, score / 100.0),
                        "metadata": {
                            "score_kind": "heuristic",
                            "signals": signals,
                            "person_track_id": person_id,
                            "vehicle_track_id": vehicle_id,
                            "near_vehicle_seconds": round(dwell, 2),
                            "reach_count": state.reach_count,
                        },
                    })
        self.interactions = {
            key: value for key, value in self.interactions.items()
            if key in active or now - value.last_seen < 30
        }
        return candidates
