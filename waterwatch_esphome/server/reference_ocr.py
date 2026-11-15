"""Private, explicitly labelled visual references; no previous-value inference.

Scores describe observed shape similarity. A new frame must match a labelled
reference in both feature views; labels are never used without image evidence.
"""
from dataclasses import dataclass

import cv2
import numpy as np

from server.analog_ocr import angular_distance


@dataclass
class ReferenceSample:
    image: np.ndarray
    value: float
    uncertainty: float = 0
    identifier: str = ""


def glyph_contrast(patch):
    gray = patch.min(axis=2)
    low, high = np.percentile(gray, (2, 98))
    gray = np.clip((gray.astype(float)-low)*255/max(1, high-low), 0, 255).astype(np.uint8)
    gray = cv2.resize(gray, (48, 80), interpolation=cv2.INTER_AREA)
    return gray


def glyph_features(patch):
    gray = glyph_contrast(patch)
    foreground = (gray >= 140).astype(np.float32)
    edges = cv2.Canny(gray, 70, 140) > 0
    return cv2.GaussianBlur(foreground, (7, 7), 1.2), edges


def edge_agreement(first, second):
    if first.sum() < 8 or second.sum() < 8:
        return 0.0
    distances = cv2.distanceTransform((~second).astype(np.uint8), cv2.DIST_L2, 3)
    reverse = cv2.distanceTransform((~first).astype(np.uint8), cv2.DIST_L2, 3)
    return min(float(np.mean(distances[first] <= 2)), float(np.mean(reverse[second] <= 2)))


def recognize_glyph(patch, samples, threshold, corroborating=()):
    foreground, edges = glyph_features(patch)
    contrast = glyph_contrast(patch).astype(np.float32)/255
    if foreground.sum() < 12:
        return None
    candidates = []
    alternatives = []
    contrast_candidates, contrast_alternatives = [], []
    for sample in samples:
        if not float(sample.value).is_integer():
            continue
        target, target_edges = glyph_features(sample.image)
        target_contrast = glyph_contrast(sample.image).astype(np.float32)/255
        target_centered = target_contrast-target_contrast.mean()
        best = (0., 0.)
        correlated = None
        label = str(int(sample.value))
        support = [(score, index) for index, (value, score) in enumerate(corroborating)
                   if value == label and score >= threshold]
        model_score, model_index = max(support, default=(0., None))
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                transform = np.float32([[1, 0, dx], [0, 1, dy]])
                moved = cv2.warpAffine(foreground, transform, (48, 80))
                moved_edges = cv2.warpAffine(edges.astype(np.uint8), transform, (48, 80)) > 0
                overlap = 2*float(np.minimum(target, moved).sum())/max(1., float(target.sum()+moved.sum()))
                edge_score = edge_agreement(target_edges, moved_edges)
                if min(overlap, edge_score) > min(best):
                    best = overlap, edge_score
                if model_index is not None and overlap >= .7 and edge_score >= max(.85, threshold):
                    shifted = cv2.warpAffine(contrast, transform, (48, 80))
                    shifted -= shifted.mean()
                    denominator = float(np.linalg.norm(shifted)*np.linalg.norm(target_centered))
                    correlation = float(np.sum(shifted*target_centered)/max(1e-9, denominator))
                    correlation = float(np.clip(correlation, -1, 1))
                    if correlation >= max(.90, threshold):
                        score = min(correlation, edge_score)
                        if correlated is None or score > correlated[0]:
                            correlated = (score, correlation, edge_score, overlap)
        alternatives.append((min(best), label))
        if min(best) >= max(.85, threshold):
            candidates.append((min(best), label, {
                "results": [(label, s) for s in best], "reference_scores": list(best),
                "reference_id": sample.identifier,
                "recognition_sources": ["Verified reference · foreground", "Verified reference · edges"]}))
        if correlated is not None:
            score, correlation, edge_score, overlap = correlated
            contrast_alternatives.append((score, label))
            contrast_candidates.append((score, label, {
                "results": [(label, model_score), (label, score)],
                "reference_id": sample.identifier, "reference_method": "contrast_model_consensus",
                "reference_model_support": {"view_index": model_index, "digit": label, "score": model_score},
                "reference_scores": [correlation, edge_score], "reference_foreground_support": overlap,
                "recognition_sources": ["Numeric model · corroborating view", "Verified reference · contrast and edges"]}))
    if not candidates:
        candidates = contrast_candidates
        alternatives.extend(contrast_alternatives)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, label, result = candidates[0]
    other = max((s for s, value in alternatives if value != label), default=0.)
    if score-other < .1:
        return None
    return {**result, "reference_policy": "verified_glyph_shape"}


def pointer_polar(patch, cfg, inner, outer):
    # Continuous opponent colour survives flash white-balance changes that move
    # the same red pigment across a hard HSV threshold. Angular NCC below removes
    # a common offset and gain; it does not rescale the resulting correlation.
    if cfg.pointer_color == "red":
        mask = np.clip((cv2.cvtColor(patch, cv2.COLOR_BGR2LAB)[:, :, 1].astype(np.float32)-128)/25, 0, 1)
    else:
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(np.float32)
        mask = np.clip(1-gray/max(1., float(np.percentile(gray, 90))), 0, 1)
    height, width = mask.shape
    angles = np.arange(720)*np.pi/360
    radii = np.linspace(inner, outer, 48)
    x = cfg.center_x*width+np.sin(angles)[:, None]*radii*cfg.radius*width
    y = cfg.center_y*height-np.cos(angles)[:, None]*radii*cfg.radius*height
    return cv2.remap(mask, x.astype(np.float32), y.astype(np.float32), cv2.INTER_LINEAR)


def circular_match(current, reference):
    first, second = reference.mean(axis=1), current.mean(axis=1)
    first = sum(np.roll(first, offset) for offset in range(-4, 5))/9
    second = sum(np.roll(second, offset) for offset in range(-4, 5))/9
    first -= first.mean()
    second -= second.mean()
    if min(float(first.std()), float(second.std())) < .015:
        return None
    frequencies = np.fft.rfft(first).conj()*np.fft.rfft(second)
    scores = np.clip(np.fft.irfft(frequencies, n=720)/(np.linalg.norm(first)*np.linalg.norm(second)), -1, 1)
    peak = int(scores.argmax())
    separation = np.abs((np.arange(720)-peak+360) % 720-360)
    runner = float(scores[separation > 24].max())
    return {"shift_degrees": peak/2, "score": float(scores[peak]),
            "peak_margin": float(scores[peak])-runner}


def head_match(current, reference, expected_angle):
    first, second = reference.mean(axis=1), current.mean(axis=1)
    first = sum(np.roll(first, offset) for offset in range(-4, 5))/9
    second = sum(np.roll(second, offset) for offset in range(-4, 5))/9
    near = np.abs((np.arange(720)-expected_angle*2+360) % 720-360) <= 70
    peak = int(np.where(near, first, -1).argmax())
    window = (np.abs((np.arange(720)-peak+360) % 720-360) <= 70).astype(float)
    count = window.sum()
    template = (first-float((first*window).sum()/count))*window
    if np.linalg.norm(template) < .015*np.sqrt(count):
        return None

    def correlate(a, b):
        return np.fft.irfft(np.fft.rfft(a).conj()*np.fft.rfft(b), n=720)

    total = correlate(window, second)
    variance = np.maximum(0, correlate(window, second*second)-total*total/count)
    norm = np.sqrt(variance*float(np.sum(template*template)))
    scores = np.divide(correlate(template, second), norm,
                       out=np.full(720, -1.), where=norm > 1e-7)
    scores[variance/count < .015**2] = -1
    scores = np.clip(scores, -1, 1)
    best = int(scores.argmax())
    separation = np.abs((np.arange(720)-best+360) % 720-360)
    return {"shift_degrees": best/2, "score": float(scores[best]),
            "peak_margin": float(scores[best]-scores[separation > 24].max())}


def recognize_pointer(patch, cfg, samples, threshold):
    bands = [(.32, .70), (.48, .84)]
    current = [pointer_polar(patch, cfg, *band) for band in bands]
    candidates = []
    for sample in samples:
        target = [pointer_polar(sample.image, cfg, *band) for band in bands]
        expected = (cfg.zero_angle_degrees+sample.value*36*(1 if cfg.clockwise else -1)) % 360
        for policy in ("whole_pointer", "needle_head"):
            matches = [circular_match(frame, known) if policy == "whole_pointer" else head_match(frame, known, expected)
                       for frame, known in zip(current, target, strict=True)]
            if any(m is None or m["score"] < max(.85, threshold) or m["peak_margin"] < .08 for m in matches):
                continue
            if angular_distance(matches[0]["shift_degrees"], matches[1]["shift_degrees"]) > 3:
                continue
            views = [{**m, "phase": (sample.value+m["shift_degrees"]*(1 if cfg.clockwise else -1)/36) % 10,
                      "uncertainty": sample.uncertainty+1/36} for m in matches]
            candidates.append((min(v["score"] for v in views), sample.identifier, views, policy))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, identifier, views, policy = candidates[0]
    # Two strong calibrations disagreeing about orientation must not be collapsed
    # to whichever fit is marginally larger.
    for _, _, alternative, _ in candidates[1:]:
        if angular_distance(views[0]["phase"]*36, alternative[0]["phase"]*36) > 12:
            return None
    return {"analog_views": views, "analog_policy": "verified_pointer_shape", "reference_id": identifier,
            "pointer_position_verified": True, "reference_match": policy,
            "recognition_sources": ["Pointer reference · inner band", "Pointer reference · outer band"]}
