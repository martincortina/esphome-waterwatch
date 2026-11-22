import math

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from server.models import DialConfig, MeterConfig, ROI, VisualReference
from server.ocr import Pipeline
from server.reference_ocr import (
    ReferenceSample,
    recognize_calibrated_glyph,
    recognize_glare_resistant_dial,
    recognize_glare_resistant_wheel,
    recognize_glyph,
    recognize_pointer,
    recognize_sector,
)


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


def dim_glyph(value):
    return np.clip(glyph(value).astype(float)*np.linspace(.1, 1., 100)[:, None, None], 0, 255).astype(np.uint8)


def test_contrast_fallback_requires_independent_numeric_support_and_preserves_its_score():
    sample = ReferenceSample(glyph(0), 0, identifier='a'*32)
    image = dim_glyph(0)
    assert recognize_glyph(image, [sample], .8) is None
    assert recognize_glyph(image, [sample], .8, [('0', .79)]) is None
    assert recognize_glyph(image, [sample], .8, [('8', .99)]) is None
    found = recognize_glyph(image, [sample], .8, [('0', .83), ('0', .4)])
    assert found['reference_method'] == 'contrast_model_consensus'
    assert found['results'][0] == ('0', .83)
    assert found['results'][1][1] >= .85
    assert found['reference_scores'][0] >= .90
    assert found['reference_foreground_support'] >= .7
    # A confident original shape match is preserved, including its actual scores.
    original = recognize_glyph(glyph(0), [sample], .8)
    assert recognize_glyph(glyph(0), [sample], .8, [('0', .83)]) == original


def test_calibrated_continuous_match_requires_both_reference_and_numeric_evidence():
    known = glyph(1)
    sample = ReferenceSample(known, 1, identifier='a'*32)
    image = np.clip(known.astype(float)*.97+2,0,255).astype(np.uint8)
    found = recognize_calibrated_glyph(image,[sample],.8,[('1',.87)])
    assert found['results'][0] == ('1',.87)
    assert found['reference_method'] == 'calibrated_contrast_gradient_consensus'
    assert found['reference_scores'][0] >= .97 and found['reference_scores'][1] >= .93
    for votes in [[],[('1',.79)],[('7',.99)]]:
        assert recognize_calibrated_glyph(image,[sample],.8,votes) is None
    assert recognize_calibrated_glyph(image,[sample,ReferenceSample(known,7)],.8,[('1',.99)]) is None
    assert recognize_calibrated_glyph(np.zeros_like(known),[sample],.8,[('1',.99)]) is None


@pytest.mark.parametrize('label',range(10))
def test_calibrated_continuous_match_rejects_other_digits_despite_incorrect_model_vote(label):
    sample=ReferenceSample(glyph(label),label)
    for other in range(10):
        if other!=label:
            assert recognize_calibrated_glyph(glyph(other),[sample],.8,[(str(label),.99)]) is None


@pytest.mark.parametrize('label', range(10))
def test_other_glyphs_reject_even_with_a_wrong_high_score_model_vote(label):
    sample = ReferenceSample(glyph(label), label)
    for value in range(10):
        if value != label:
            assert recognize_glyph(glyph(value), [sample], .8, [(str(label), .99)]) is None
            assert recognize_glyph(dim_glyph(value), [sample], .8, [(str(label), .99)]) is None


def test_pipeline_contrast_corroboration_keeps_actual_model_score():
    from server.neural_ocr import RowRecognition

    class Engine:
        def recognize_digits(self, patches, valid):
            return RowRecognition([[('0', .83), ('0', .4)]], patches[0], {
                'recognition_evidence': [{'primary_recognitions': [('0', .83), ('0', .4)]}]})

    cfg = MeterConfig(id='a', name='a', decimal_places=0, min_confidence=.8,
        rois=[ROI(x=0, y=0, w=1, h=1, visual_references=[VisualReference(id='a'*32, value=0)])])
    pipeline = Pipeline(Engine(), recognition_loader=lambda _, i: [ReferenceSample(glyph(0), 0)])
    data = cv2.imencode('.png', dim_glyph(0))[1].tobytes()
    result = pipeline.run(data, cfg)
    assert result.raw_value == '0' and result.confidence == .83
    assert result.details['digits'][0]['status'] == 'reference_verified'
    assert result.details['digits'][0]['reference_method'] == 'contrast_model_consensus'


def test_contrast_corroboration_cannot_bypass_a_weak_rolling_phase():
    from server.neural_ocr import RowRecognition

    class Engine:
        def recognize_digits(self, patches, valid):
            results = [[('1', .99), ('1', .98)], [('0', .83), ('0', .4)]]
            return RowRecognition(results, patches[0], {
                'recognition_evidence': [{'primary_recognitions': views} for views in results]})

    class Wheel:
        def recognize(self, patch):
            return [{'phase': 0., 'completed_digit': '0', 'score': .5, 'center_score': .5}]*2

    cfg = MeterConfig(id='a', name='a', decimal_places=1, min_confidence=.8,
                      last_decimal_mode='rolling', rois=[ROI(x=0, y=0, w=.5, h=1),
        ROI(x=.5, y=0, w=.5, h=1, visual_references=[VisualReference(id='a'*32, value=0)])])
    pipeline = Pipeline(Engine(), wheel_engine=Wheel(),
                        recognition_loader=lambda _, i: [ReferenceSample(glyph(0), 0)] if i==1 else [])
    data = cv2.imencode('.png', np.hstack([glyph(1), dim_glyph(0)]))[1].tobytes()
    result = pipeline.run(data, cfg)
    assert result.raw_value == '1.?' and result.confidence == 0
    assert result.details['digits'][1]['status'] == 'rolling_uncertain'


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


def test_sector_reference_classifies_only_matching_absolute_pointer_poses():
    image=pointer(6.4*36)
    sample=ReferenceSample(image,6,identifier='a'*32,kind='sector')
    result=recognize_sector(image,DialConfig(),[sample],.8)
    assert [v for v,s in result['results']]==['6','6']
    assert min(s for v,s in result['results'])>=.97
    assert recognize_pointer(image,DialConfig(),[sample],.8) is None
    for digit in [0,1,2,3,4,5,7,8,9]:
        assert recognize_sector(pointer((digit+.4)*36),DialConfig(),[sample],.8) is None
    assert recognize_sector(image,DialConfig(),[sample,ReferenceSample(image,5,kind='sector')],.8) is None


def test_glare_resistant_wheel_checks_the_visible_glyph_and_both_phases():
    image = glyph(2)
    reflected = image.copy()
    reflected[-16:, -10:] = 255
    samples = [ReferenceSample(image, 2, identifier="2")]
    views = [{"phase": 2.4, "score": .93}, {"phase": 2.5, "score": .88}]
    result = recognize_glare_resistant_wheel(reflected, samples, .8, views)
    assert [value for value, _ in result["results"]] == ["2", "2"]
    assert recognize_glare_resistant_wheel(reflected, samples, .8, views[:1]) is None
    assert recognize_glare_resistant_wheel(reflected, samples, .8, [views[0], {"phase": 3.1, "score": .92}]) is None
    assert recognize_glare_resistant_wheel(reflected, samples + [ReferenceSample(image, 3)], .8, views) is None


def test_glare_resistant_dial_requires_separate_images_and_rejects_conflicting_labels():
    image = pointer(6.4 * 36)
    lighter = np.minimum(image.astype(np.int16) + 5, 255).astype(np.uint8)
    cfg = DialConfig()
    first = ReferenceSample(image, 6, identifier="first", kind="sector")
    second = ReferenceSample(lighter, 6, identifier="second", kind="sector")
    duplicate = ReferenceSample(image.copy(), 6, identifier="duplicate", kind="sector")
    conflict = ReferenceSample(lighter, 5, identifier="conflict", kind="sector")
    assert recognize_glare_resistant_dial(image, cfg, [first, duplicate], .8) is None
    result = recognize_glare_resistant_dial(image, cfg, [first, second], .8)
    assert [value for value, _ in result["results"]] == ["6", "6"]
    assert recognize_glare_resistant_dial(image, cfg, [first, second, conflict], .8) is None


def test_pointer_prefers_precise_calibration_over_higher_scoring_coarse_label(monkeypatch):
    import server.reference_ocr as module
    monkeypatch.setattr(module,'pointer_polar',lambda image,*args:np.full((720,48),image[0,0,0],np.float32))
    def match(current,known):
        coarse=known[0,0]==10
        return {'shift_degrees':216. if coarse else 21.6,'score':.99 if coarse else .96,'peak_margin':.2}
    monkeypatch.setattr(module,'circular_match',match)
    monkeypatch.setattr(module,'head_match',lambda *args:None)
    samples=[ReferenceSample(np.full((100,100,3),10,np.uint8),4.5,.49,'coarse'),
             ReferenceSample(np.full((100,100,3),20,np.uint8),0,.2,'precise')]
    result=recognize_pointer(pointer(20),DialConfig(),samples,.8)
    assert result['reference_id']=='precise'
    assert all(view['score']==.96 and abs(view['phase']-.6)<1e-9 for view in result['analog_views'])
    assert all(view['uncertainty']<.23 for view in result['analog_views'])


def test_included_dial_can_use_a_verified_sector_without_inventing_a_precise_angle():
    cfg=MeterConfig(id='a',name='a',decimal_places=1,rois=[ROI(x=0,y=0,w=.5,h=1),
        ROI(x=.5,y=0,w=.5,h=1,kind='analog',visual_references=[VisualReference(id='a'*32,value=6,kind='sector')])])
    class Text:
        def digit(self,patch):return '1',.99
    image=pointer(6.4*36)
    sample=ReferenceSample(image,6,identifier='a'*32,kind='sector')
    data=cv2.imencode('.png',np.hstack([cv2.resize(glyph(1),(160,160)),image]))[1].tobytes()
    result=Pipeline(Text(),recognition_loader=lambda cfg,i:[sample] if i==1 else []).run(data,cfg)
    assert result.raw_value=='1.6'
    dial=result.details['digits'][1]
    assert dial['status']=='analog_verified' and dial['analog_policy']=='verified_sector_reference'
    assert 'analog_views' not in dial and 'pointer_position_verified' not in dial


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
    # New recognition choices do not invalidate already saved private crops.
    cfg.rois[0].recognition_mode='calibrated'
    assert len(client.service.load_visual_references(cfg,0))==1
    # References written before the optional kind field remain readable.
    import json
    metadata=client.service.references/f"recognition-house-{cfg.rois[0].visual_references[0].id}.json"
    legacy=json.loads(metadata.read_text(encoding='utf-8'));legacy['label'].pop('kind')
    metadata.write_text(json.dumps(legacy),encoding='utf-8')
    assert len(client.service.load_visual_references(cfg,0))==1
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


def test_sector_reference_api_preserves_label_kind_and_does_not_record(client):
    cfg=MeterConfig(id='house',name='House',rois=[ROI(x=0,y=0,w=1,h=1,kind='analog',include_in_reading=False)])
    client.service.save_config(cfg)
    before={table:client.service.store.all(f'SELECT * FROM {table}') for table in ['readings','outbox']}
    data=cv2.imencode('.png',pointer(6.4*36))[1].tobytes()
    response=client.post('/api/meters/house/recognition-reference?region=0&value=6&kind=sector',content=data)
    assert response.status_code==200
    cfg.rois[0].visual_references=[VisualReference.model_validate(response.json()['reference'])]
    assert cfg.rois[0].visual_references[0].kind=='sector'
    assert client.put('/api/meters/house',json=cfg.model_dump()).status_code==200
    assert client.service.load_visual_references(cfg,0)[0].kind=='sector'
    for query in ['value=6.2&kind=sector','value=6&kind=sector&uncertainty=.1','value=6&kind=unknown']:
        assert client.post('/api/meters/house/recognition-reference?region=0&'+query,content=data).status_code==422
    assert {table:client.service.store.all(f'SELECT * FROM {table}') for table in before}==before
