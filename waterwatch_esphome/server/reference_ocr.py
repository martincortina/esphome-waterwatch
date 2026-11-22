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
    kind: str = "position"


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


def recognize_calibrated_glyph(patch, samples, threshold, corroborating):
    """Require model support and two continuous image matches for learned wheels.

    Binary edges can change sharply with JPEG noise on a thin reflective digit.
    Contrast and signed gradients remain continuous. Neither a text vote alone
    nor an unlabelled phase estimate can enable this private-reference route.
    """
    def features(image):
        contrast = cv2.GaussianBlur(glyph_contrast(image).astype(np.float32)/255,
                                    (3, 3), .7)
        gradient = np.stack([cv2.Sobel(contrast, cv2.CV_32F, 1, 0),
                             cv2.Sobel(contrast, cv2.CV_32F, 0, 1)], axis=2)
        return contrast, gradient

    def correlation(first, second):
        # Do not reward padded borders introduced by the translation search.
        first, second = first[3:-3, 3:-3], second[3:-3, 3:-3]
        first, second = first-first.mean(), second-second.mean()
        denominator = float(np.linalg.norm(first)*np.linalg.norm(second))
        return float(np.clip(np.sum(first*second)/max(1e-9, denominator), -1, 1))

    contrast, gradient = features(patch)
    if float(contrast.std()) < .03:
        return None
    candidates, alternatives = [], []
    for sample in samples:
        if sample.kind != "position" or sample.uncertainty or not float(sample.value).is_integer():
            continue
        label = str(int(sample.value))
        model_score = max((score for value, score in corroborating if value == label), default=0.)
        target, target_gradient = features(sample.image)
        best = (0., 0.)
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                transform = np.float32([[1, 0, dx], [0, 1, dy]])
                moved = cv2.warpAffine(contrast, transform, (48, 80))
                moved_gradient = cv2.warpAffine(gradient, transform, (48, 80))
                scores = correlation(moved, target), correlation(moved_gradient, target_gradient)
                if min(scores) > min(best):
                    best = scores
        alternatives.append((min(best), label))
        if model_score >= threshold and best[0] >= max(.97, threshold) and best[1] >= max(.93, threshold):
            candidates.append((min(best), label, {
                "results": [(label, model_score), (label, min(best))],
                "reference_id": sample.identifier,
                "reference_policy": "verified_glyph_shape",
                "reference_method": "calibrated_contrast_gradient_consensus",
                "reference_scores": list(best), "reference_model_score": model_score,
                "recognition_sources": ["Numeric model · corroborating view",
                                        "Verified reference · contrast and gradients"]}))
    if not candidates:
        return None
    score, label, result = max(candidates, key=lambda item: item[0])
    if score-max((s for s, other in alternatives if other != label), default=0.) < .1:
        return None
    return result


def unit_profile(values):
    values = np.asarray(values, dtype=np.float32).ravel()
    centered = values - values.mean()
    return centered / max(np.linalg.norm(centered), 1e-9)


def wheel_glare_profile(patch):
    gray = cv2.resize(patch.min(axis=2), (48, 80), interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(gray, (3, 3), .7)
    # The lower corner of a rolling wheel often reflects the flash off the glass.
    glyph = gray[4:65, 4:41]
    gradients = np.dstack([cv2.Sobel(glyph, cv2.CV_32F, x, y) for x, y in ((1, 0), (0, 1))])
    return unit_profile(glyph), unit_profile(gradients)


def recognizable_pose(candidates, minimum, support_count, threshold):
    ranked = sorted(candidates, key=lambda item: item[0], reverse=True)
    if not ranked:
        return []
    label = ranked[0][1]
    support = []
    signatures = set()
    for score, digit, views, sample in ranked:
        if digit != label or score < minimum or min(views) < threshold:
            continue
        signature = sample.image.tobytes()
        if signature not in signatures:
            signatures.add(signature)
            support.append((score, digit, views, sample))
        if len(support) == support_count:
            break
    other = max((score for score, digit, _, _ in ranked if digit != label), default=-1.)
    if len(support) < support_count or support[-1][0] - other < .08:
        return []
    return support


def pose_score(scores, floors):
    return min((score - floor) / (1 - floor) for score, floor in zip(scores, floors, strict=True))


def recognize_glare_resistant_wheel(patch, samples, threshold, phase_views):
    """Cross-check a reference outside the glare against both wheel model views."""
    if len(phase_views) != 2:
        return None
    current = wheel_glare_profile(patch)
    candidates = []
    floors = (.90, .78)
    for sample in samples:
        if sample.kind != "position" or sample.uncertainty or not float(sample.value).is_integer():
            continue
        target = wheel_glare_profile(sample.image)
        views = [float(first @ second) for first, second in zip(current, target, strict=True)]
        candidates.append((pose_score(views, floors), str(int(sample.value)), views, sample))
    support = recognizable_pose(candidates, .2, 1, threshold)
    if not support:
        return None
    _, label, views, sample = support[0]
    model_score = min(view["score"] for view in phase_views)
    if model_score < threshold or any(str(int(view["phase"])) != label for view in phase_views):
        return None
    return {"results": [(label, model_score), (label, min(views))], "reference_id": sample.identifier,
            "reference_policy": "verified_glyph_shape", "reference_method": "glare_resistant_wheel_consensus",
            "reference_scores": views, "reference_model_score": model_score,
            "recognition_sources": ["Wheel · phase agreement", "Verified reference · glare-free glyph"]}


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
        if sample.kind != "position":
            continue
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
    # A broad, approximate label must not displace a more precise calibration
    # merely because its image correlation is marginally higher. Every candidate
    # already passed the same score, peak-separation and two-band angle guards.
    candidates.sort(key=lambda item: (max(v["uncertainty"] for v in item[2]), -item[0]))
    _, identifier, views, policy = candidates[0]
    # Two strong calibrations disagreeing about orientation must not be collapsed
    # to whichever fit is marginally larger.
    for _, _, alternative, _ in candidates[1:]:
        if angular_distance(views[0]["phase"]*36, alternative[0]["phase"]*36) > 12:
            return None
    return {"analog_views": views, "analog_policy": "verified_pointer_shape", "reference_id": identifier,
            "pointer_position_verified": True, "reference_match": policy,
            "recognition_sources": ["Pointer reference · inner band", "Pointer reference · outer band"]}


def recognize_sector(patch, cfg, samples, threshold):
    """Classify a physically labelled sector at the same observed pointer pose.

    This is an absolute-angle pattern comparison: do not rotate a known digit
    around the dial and reuse its label. Both bands need strong correlation;
    competing sector labels and empty colour profiles remain unresolved.
    """
    bands = [(.32, .70), (.48, .84)]
    current = [pointer_polar(patch, cfg, *band).mean(axis=1) for band in bands]
    candidates = []
    for sample in samples:
        if sample.kind != "sector":
            continue
        target = [pointer_polar(sample.image, cfg, *band).mean(axis=1) for band in bands]
        best = (0., 0.)
        for shift in range(-2, 3):  # at most one degree of sampling/alignment jitter
            scores = []
            for actual, known in zip(current, target, strict=True):
                a, b = np.roll(actual, shift)-actual.mean(), known-known.mean()
                if min(float(a.std()), float(b.std())) < .015:
                    scores.append(0.)
                    continue
                score = float(np.sum(a*b)/max(1e-9, np.linalg.norm(a)*np.linalg.norm(b)))
                scores.append(float(np.clip(score, -1, 1)))
            if min(scores) > min(best):
                best = tuple(scores)
        candidates.append((min(best), str(int(sample.value)), best, sample.identifier))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    score, label, views, identifier = candidates[0]
    other = max((s for s, value, _, _ in candidates if value != label), default=0.)
    if score < max(.97, threshold) or score-other < .1:
        return None
    return {"results": [(label, support) for support in views],
            "analog_policy": "verified_sector_reference", "sector_reference_id": identifier,
            "recognition_sources": ["Verified sector · inner band", "Verified sector · outer band"],
            "sector_reference_scores": list(views)}


def dial_pose_profile(patch, cfg):
    intensity = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32)
    height, width = intensity.shape
    angles = np.arange(720) * np.pi / 360
    profiles = []
    for lower, upper in ((.32, .69), (.52, .86)):
        radii = np.linspace(lower, upper, 32)
        x = cfg.center_x * width + np.sin(angles)[:, None] * radii * cfg.radius * width
        y = cfg.center_y * height - np.cos(angles)[:, None] * radii * cfg.radius * height
        color = pointer_polar(patch, cfg, lower, upper).mean(axis=1)
        light = cv2.remap(intensity, x.astype(np.float32), y.astype(np.float32), cv2.INTER_LINEAR).mean(axis=1)
        for channel in (color, light):
            profiles.append(unit_profile(sum(np.roll(channel, offset) for offset in range(-4, 5)) / 9))
    return np.array(profiles)


def recognize_glare_resistant_dial(patch, cfg, samples, threshold):
    """Require two distinct labeled images with matching color and intensity."""
    if cfg.pointer_color != "red":
        return None
    current = dial_pose_profile(patch, cfg)
    candidates = []
    floors = (.88, .95)
    for sample in samples:
        if sample.kind != "sector":
            continue
        views = np.sum(current * dial_pose_profile(sample.image, cfg), axis=1)
        scores = [float(min(views[::2])), float(min(views[1::2]))]
        candidates.append((pose_score(scores, floors), str(int(sample.value)), scores, sample))
    support = recognizable_pose(candidates, .5, 2, threshold)
    if not support:
        return None
    _, label, views, sample = support[0]
    return {"results": [(label, min(first, second)) for first, second in zip(views, support[1][2], strict=True)],
            "analog_policy": "verified_sector_consensus", "sector_reference_id": sample.identifier,
            "sector_reference_ids": [row[3].identifier for row in support],
            "recognition_sources": ["Verified sector · pointer colour", "Verified sector · light and shadow"],
            "sector_reference_scores": views}


def dial_edge_profile(patch, cfg):
    profiles = dial_pose_profile(patch, cfg)
    edges = [unit_profile(np.roll(profile, -3) - np.roll(profile, 3)) for profile in profiles]
    return np.concatenate((profiles, np.stack(edges)))


def recognize_dial_edge_match(patch, cfg, samples, threshold):
    """Distinguish nearby tick poses only when one labeled match is clearly closer."""
    if cfg.pointer_color != "red":
        return None
    current = dial_edge_profile(patch, cfg)
    matches = []
    luminance_matches = []
    for sample in samples:
        if sample.kind != "sector":
            continue
        profile = dial_edge_profile(sample.image, cfg)
        similarities = np.clip(np.sum(current * profile, axis=1), -1, 1)
        color = float(min(similarities[0], similarities[2]))
        light = float(min(similarities[1], similarities[3]))
        edge = float(min(similarities[4:]))
        error = .8 * (.55 * (1 - color) + .45 * (1 - light)) + .2 * (1 - edge)
        reliable = color >= max(.94, threshold) and light >= max(.97, threshold) and edge >= .76
        label = str(int(sample.value))
        matches.append((error, label, sample, color, light, edge, reliable))
        light_edge = float(min(similarities[5], similarities[7]))
        signals = [color, light, light_edge]
        luminance_matches.append((pose_score(signals, (.82, .988, .922)), label, signals, sample))
    best = min((item for item in matches if item[-1]), key=lambda item: item[0], default=None)
    if best is not None:
        error, label, sample, color, light, edge, _ = best
        competing = min((item[0] for item in matches if item[1] != label), default=None)
        if competing is not None and error + .001 < .55 * max(competing, .001):
            return {"results": [(label, color), (label, light)], "analog_policy": "verified_sector_edge_match",
                    "sector_reference_id": sample.identifier, "sector_reference_scores": [color, light, edge],
                    "recognition_sources": ["Verified sector · pointer colour", "Verified sector · edge contrast"]}

    # Reflections move the edges in the red mask even when the underlying dial is
    # still. Require two distinct references with the same light and shadow edges
    # in both radial bands, with independent colour support and no nearby rival.
    support = recognizable_pose(luminance_matches, .5, 2, threshold)
    if not support or not any(label != support[0][1] for _, label, _, _ in luminance_matches):
        return None
    _, label, scores, sample = support[0]
    color, light, edge = [min(a, b) for a, b in zip(scores, support[1][2], strict=True)]
    return {"results": [(label, color), (label, min(light, edge))], "analog_policy": "verified_sector_luminance_consensus",
            "sector_reference_id": sample.identifier, "sector_reference_ids": [row[3].identifier for row in support],
            "sector_reference_scores": [color, light, edge],
            "recognition_sources": ["Verified sector · pointer colour", "Verified sector · luminance edges"]}
