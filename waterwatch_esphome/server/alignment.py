"""Reference-based rigid registration. Uncertain matches abstain before OCR."""
import math

import cv2
import numpy as np


class AlignmentError(ValueError):
    def __init__(self, reason, details=None):
        super().__init__(reason)
        self.details = {"enabled": True, "status": reason, **(details or {})}


def register(image, reference, cfg):
    options = cfg.alignment
    if reference is None:
        raise AlignmentError("alignment_reference_missing")
    if image.shape != reference.shape:
        raise AlignmentError("alignment_image_size_changed")
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    target = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    source_points, target_points, matches = [], [], []
    for index, roi in enumerate(options.anchors):
        x, y, w, h = int(roi.x*width), int(roi.y*height), int(roi.w*width), int(roi.h*height)
        template = target[y:y+h, x:x+w]
        if min(w, h) < 8 or float(template.std()) < 8:
            raise AlignmentError("alignment_landmark_low_contrast", {"landmark": index+1})
        dx, dy = math.ceil(width*options.search_margin), math.ceil(height*options.search_margin)
        left, top, right, bottom = max(0, x-dx), max(0, y-dy), min(width, x+w+dx), min(height, y+h+dy)
        scores = cv2.matchTemplate(gray[top:bottom, left:right], template, cv2.TM_CCOEFF_NORMED)
        _, score, _, position = cv2.minMaxLoc(scores)
        px, py = position
        # Ignore the immediate peak neighbourhood, then reject similar alternatives.
        alternatives = scores.copy()
        rx, ry = max(2, w//3), max(2, h//3)
        alternatives[max(0, py-ry):py+ry+1, max(0, px-rx):px+rx+1] = -1
        margin = score-float(alternatives.max())
        match = {"landmark": index+1, "score": round(score, 4), "peak_margin": round(margin, 4)}
        matches.append(match)
        if not np.isfinite(score) or score < options.min_score:
            raise AlignmentError("alignment_landmark_not_found", {"landmarks": matches})
        if margin < options.min_peak_margin:
            raise AlignmentError("alignment_landmark_ambiguous", {"landmarks": matches})
        source_points.append((left+px+w/2, top+py+h/2))
        target_points.append((x+w/2, y+h/2))
    if len(source_points) != 2:
        raise AlignmentError("alignment_landmarks_missing")
    source, destination = np.float64(source_points), np.float64(target_points)
    a, b = source[1]-source[0], destination[1]-destination[0]
    if np.linalg.norm(a) < 8:
        raise AlignmentError("alignment_landmark_ambiguous")
    scale_ratio = float(np.linalg.norm(b)/np.linalg.norm(a))
    angle = math.atan2(b[1], b[0])-math.atan2(a[1], a[0])
    angle = (angle+math.pi) % (2*math.pi)-math.pi
    if abs(math.degrees(angle)) > options.max_angle_degrees or abs(scale_ratio-1) > .025:
        raise AlignmentError("alignment_movement_too_large", {"scale_ratio": scale_ratio, "rotation_degrees": math.degrees(angle)})
    cosine, sine = math.cos(angle), math.sin(angle)
    rotation = np.array([[cosine, -sine], [sine, cosine]])
    translation = destination.mean(axis=0)-rotation @ source.mean(axis=0)
    matrix = np.column_stack([rotation, translation])
    # A registered white border must never be mistaken for a real digit region.
    from server.ocr import digit_corners
    inverse = cv2.invertAffineTransform(matrix)
    for index, roi in enumerate(cfg.rois):
        points = digit_corners(roi, width, height) @ inverse[:, :2].T + inverse[:, 2]
        if np.any(points < -.001) or np.any(points > np.array([width, height])+.001):
            raise AlignmentError("alignment_digit_outside_image", {"digit": index+1})
    aligned = cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_CUBIC,
                             borderValue=(255, 255, 255))
    return aligned, {"enabled": True, "status": "aligned", "landmarks": matches,
                     "rotation_degrees": round(math.degrees(angle), 3),
                     "translation_pixels": np.round(translation, 2).tolist(),
                     "scale_ratio": round(scale_ratio, 4)}
