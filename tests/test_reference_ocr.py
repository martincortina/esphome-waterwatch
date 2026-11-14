import math

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from server.models import DialConfig, MeterConfig, ROI, VisualReference
from server.ocr import Pipeline
from server.reference_ocr import ReferenceSample, recognize_glyph, recognize_pointer


def glyph(value):
    image = np.full((100, 60, 3), 20, np.uint8)
    cv2.putText(image, str(value), (4, 82), cv2.FONT_HERSHEY_SIMPLEX, 2.5, (210, 210, 210), 5)
    return image


def pointer(angle, second=False, color=(20, 20, 190)):
    image = np.full((160, 160, 3), 80, np.uint8)
    cv2.circle(image, (80, 80), 71, (210, 210, 210), 2)
    for degrees in [angle]+([angle+180] if second else []):
        a = math.radians(degrees)
        tip = (round(80+64*math.sin(a)), round(80-64*math.cos(a)))
        cv2.arrowedLine(image, (80, 80), tip, color, 8, tipLength=.25)
    return image


def distance(a, b):
    return abs((a-b+5) % 10-5)


def test_glyph_requires_measured_shape_and_reports_original_similarity():
    known = glyph(1)
    samples = [ReferenceSample(known, 1, identifier='a'*32)]
    moved = cv2.warpAffine(known, np.float32([[1, 0, 1], [0, 1, 0]]), (60, 100), borderValue=(20, 20, 20))
    found = recognize_glyph(moved, samples, .8)
    assert [value for value, _ in found['results']] == ['1', '1']
    assert all(.85 <= score <= 1 for _, score in found['results'])
    for image in [glyph(0), glyph(2), glyph(7), np.zeros_like(known), np.random.default_rng(42).integers(0, 255, known.shape, dtype=np.uint8)]:
        assert recognize_glyph(image, samples, .8) is None
    assert recognize_glyph(known, [ReferenceSample(known, 1), ReferenceSample(known, 7)], .8) is None


@pytest.mark.parametrize('value', [n+.35 for n in range(10)])
@pytest.mark.parametrize('clockwise', [True, False])
def test_pointer_follows_rotation_instead_of_repeating_label(value, clockwise):
    cfg = DialConfig(clockwise=clockwise)
    sign = 1 if clockwise else -1
    samples = [ReferenceSample(pointer(0), 0, .1, 'a'*32)]
    result = recognize_pointer(pointer(value*36*sign, color=(65, 25, 160)), cfg, samples, .8)
    assert result is not None
    assert all(distance(view['phase'], value) < .08 for view in result['analog_views'])
    assert min(v['score'] for v in result['analog_views']) >= .85


def test_pointer_blanks_competing_needles_and_conflicting_labels_reject():
    samples = [ReferenceSample(pointer(0), 0, .2, 'a'*32)]
    for image in [np.full((160, 160, 3), 80, np.uint8), pointer(0, second=True)]:
        assert recognize_pointer(image, DialConfig(), samples, .8) is None
    assert recognize_pointer(pointer(0), DialConfig(), samples+[ReferenceSample(pointer(0), 4, .2)], .8) is None


def test_reference_labels_are_strict_and_bounded():
    with pytest.raises(ValidationError):
        ROI(x=0, y=0, w=1, h=1, visual_references=[VisualReference(id='a'*32, value=1.2)])
    with pytest.raises(ValidationError):
        VisualReference(id='../escape', value=1)
    with pytest.raises(ValidationError):
        VisualReference(id='a'*32, value=10)
    with pytest.raises(ValidationError):
        VisualReference(id='a'*32, value=0, uncertainty=.5)


class WeakText:
    def digit(self, patch):
        return None, 0


def test_pipeline_uses_image_evidence_and_keeps_boundary_dial_out_of_total():
    ref = VisualReference(id='a'*32, value=0, uncertainty=.2)
    cfg = MeterConfig(id='a', name='a', decimal_places=0,
        rois=[ROI(x=0, y=0, w=1, h=1, kind='analog', include_in_reading=False, visual_references=[ref])])
    image = pointer(0)
    data = cv2.imencode('.png', image)[1].tobytes()
    pipeline = Pipeline(WeakText(), recognition_loader=lambda cfg, i: [ReferenceSample(image, 0, .2)])
    result = pipeline.run(data, cfg)
    digit = result.details['digits'][0]
    assert result.raw_value is None and result.reason == 'preview_only'
    assert digit['status'] == 'analog_position_verified' and digit['analog_digit_boundary']
    assert all(value is None for value, _ in digit['recognitions'])
    cfg.rois[0].include_in_reading = True
    included = pipeline.run(data, cfg)
    assert included.raw_value == '?' and included.details['digits'][0]['status'] == 'analog_uncertain'


def test_reference_api_is_private_geometry_bound_and_does_not_change_ledger(client):
    cfg = MeterConfig(id='house', name='House', decimal_places=0, rois=[ROI(x=0, y=0, w=1, h=1)])
    client.service.save_config(cfg)
    data = cv2.imencode('.png', glyph(1))[1].tobytes()
    before = {table: client.service.store.all(f'SELECT * FROM {table}') for table in ['readings', 'outbox']}
    path = '/api/meters/house/recognition-reference?region=0&value=1'
    assert client.post(path, content=data, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    response = client.post(path, content=data)
    assert response.status_code == 200
    assert client.service.store.config('house') == cfg
    cfg.rois[0].visual_references = [VisualReference.model_validate(response.json()['reference'])]
    assert client.put('/api/meters/house', json=cfg.model_dump()).status_code == 200
    samples = client.service.load_visual_references(cfg, 0)
    assert len(samples) == 1 and np.array_equal(samples[0].image, glyph(1))
    cfg.rois[0].x = .01
    cfg.rois[0].w = .99
    assert not client.service.load_visual_references(cfg, 0)
    cfg.rois[0].x = 0
    cfg.rois[0].w = 1
    cfg.rois[0].visual_references[0].value = 2
    assert not client.service.load_visual_references(cfg, 0)
    for query in ['region=12&value=1', 'region=0&value=1.2', 'region=0&value=10']:
        assert client.post('/api/meters/house/recognition-reference?'+query, content=data).status_code == 422
    assert {table: client.service.store.all(f'SELECT * FROM {table}') for table in before} == before
