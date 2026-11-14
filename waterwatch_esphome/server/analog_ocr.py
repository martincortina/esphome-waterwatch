"""Calibrated 0..9 pointer dials, measured geometrically without model downloads.

Two annular bands must identify the same directional pointer. The returned score
measures geometric separation from competing rays, not an accuracy probability.
"""
import math

import cv2
import numpy as np

from server.models import DialConfig

ANGLES = 720


def angular_distance(a, b):
    return abs((a-b+180) % 360-180)


def pointer_mask(patch, color):
    if color == "red":
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        return (((hsv[:, :, 0] <= 12) | (hsv[:, :, 0] >= 168))
                & (hsv[:, :, 1] >= 80) & (hsv[:, :, 2] >= 40)).astype(np.float32)
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    level, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    return (gray < np.clip(level, 25, 140)).astype(np.float32)


def band_evidence(mask, cfg, inner, outer):
    height, width = mask.shape
    angles = np.arange(ANGLES)*2*np.pi/ANGLES
    radii = np.linspace(inner, outer, 48)
    x = cfg.center_x*width + np.sin(angles)[:, None]*radii*cfg.radius*width
    y = cfg.center_y*height - np.cos(angles)[:, None]*radii*cfg.radius*height
    sampled = cv2.remap(mask, x.astype(np.float32), y.astype(np.float32), cv2.INTER_LINEAR)
    rays = sampled.mean(axis=1)
    rays = sum(np.roll(rays, offset) for offset in range(-3, 4))/7
    peak = int(rays.argmax())
    strength = float(rays[peak])
    # Find the whole connected angular lobe. A broad triangular pointer must not
    # compete against its own shoulder as if it were a second needle.
    active = rays >= max(.15, strength*.5)
    left = right = peak
    while peak-left < ANGLES and active[(left-1) % ANGLES]:
        left -= 1
    while right-left < ANGLES and active[(right+1) % ANGLES]:
        right += 1
    members = np.arange(left, right+1) % ANGLES
    outside = np.ones(ANGLES, dtype=bool)
    outside[np.arange(left-12, right+13) % ANGLES] = False
    outside &= np.abs((np.arange(ANGLES)-peak+ANGLES/2) % ANGLES-ANGLES/2) > 40
    runner = float(rays[outside].max()) if outside.any() else strength
    lobe = np.zeros(ANGLES)
    lobe[members] = rays[members]
    angle = math.degrees(math.atan2(float(np.sum(lobe*np.sin(angles))),
                                    float(np.sum(lobe*np.cos(angles))))) % 360
    score = strength/max(1e-9, strength+runner)
    lobe_width = (right-left+1)*360/ANGLES
    if strength < .55 or lobe_width > 110:
        score = 0.0
    phase = ((angle-cfg.zero_angle_degrees)*(1 if cfg.clockwise else -1)) % 360/36
    return {"angle_degrees": round(angle, 3), "phase": round(phase, 4),
            "score": round(score, 6), "radial_support": round(strength, 6),
            "pointer_width_degrees": lobe_width}


def recognize_dial(patch, cfg: DialConfig):
    if min(patch.shape[:2]) < 32:
        return [(None, 0.0)]*2, {"analog_policy": "region_too_small", "analog_views": []}
    mask = pointer_mask(patch, cfg.pointer_color)
    views = [band_evidence(mask, cfg, .32, .70), band_evidence(mask, cfg, .48, .84)]
    evidence = {"analog_views": views, "analog_policy": "uncertain",
                "recognition_sources": ["Pointer · inner band", "Pointer · outer band"],
                "pointer_color": cfg.pointer_color}
    if angular_distance(views[0]["angle_degrees"], views[1]["angle_degrees"]) > 4:
        return [(None, 0.0)]*2, evidence
    # A +/- 0.08 digit guard prevents angle jitter from changing a completed digit.
    if any(min(v["phase"] % 1, 1-v["phase"] % 1) < .08 for v in views):
        evidence["analog_policy"] = "near_digit_boundary"
        return [(None, 0.0)]*2, evidence
    digits = [str(int(v["phase"]) % 10) for v in views]
    if digits[0] != digits[1] or any(v["score"] == 0 for v in views):
        return [(None, 0.0)]*2, evidence
    evidence["analog_policy"] = "two_band_measurement"
    return [(digit, view["score"]) for digit, view in zip(digits, views, strict=True)], evidence
