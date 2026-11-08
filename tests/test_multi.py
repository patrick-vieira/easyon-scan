import cv2 as cv
import numpy as np
import pytest
from easyon_scan.scan import measure, ScanError
from easyon_scan.multi import grid_calibration
from easyon_scan.dxf import dxf_bytes
from easyon_scan.references import get_reference


def scene():
    image=np.full((900,1200,3),25,np.uint8)
    # 5 px/mm, nominal J26 and J25 silhouettes, and an L with known outer arms.
    cv.rectangle(image,(50,100),(175,510),(160,70,40),-1)
    cv.rectangle(image,(250,100),(360,410),(210,140,70),-1)
    poly=np.int32([[470,150],[495,150],[495,600],[680,600],[680,625],[470,625]])
    cv.fillPoly(image,[poly],(210,210,210))
    cv.rectangle(image,(800,180),(1050,530),(230,230,230),-1)
    return image


def test_all_objects_l_sides_no_automatic_mini_assignment():
    result=measure(scene(),reference='bic',check_reference='bic-mini',all_objects=True)
    data=result.summary()
    assert len(data['objects'])==3
    key=next(o for o in data['objects'] if o['kind']=='chave L/objeto fino')
    assert len(key['sides_mm'])==6
    assert key['length_mm']==pytest.approx(95,abs=1)
    assert max(key['sides_mm'])==pytest.approx(95,abs=1)
    assert data['check_reference'] is None
    assert b'PIECE_001' in dxf_bytes(result)
    assert b'PIECE_002' in dxf_bytes(result)
    assert get_reference('bic-mini').accuracy=='approximate-scale-only'


def test_multi_requires_reference_and_valid_pitch():
    with pytest.raises(ScanError): measure(scene(),all_objects=True)
    with pytest.raises(ValueError): measure(scene(),reference='bic',all_objects=True,grid_pitch_mm=0)
    with pytest.raises(ValueError): measure(scene(),reference='bic',check_reference='bic-mini')


def test_grid_local_homography_and_pitch():
    image=np.full((700,700,3),30,np.uint8)
    cv.rectangle(image,(100,100),(600,600),(230,230,230),-1)
    for k in range(125,600,25):
        cv.line(image,(k,100),(k,600),(100,100,100),2)
        cv.line(image,(100,k),(600,k),(100,100,100),2)
    contour=np.int32([[100,100],[600,100],[600,600],[100,600]]).reshape(-1,1,2)
    src=np.float32([[0,0],[699,0],[699,699],[0,699]])
    dst=np.float32([[70,15],[650,80],[690,660],[15,690]])
    H=cv.getPerspectiveTransform(src,dst)
    warped=cv.warpPerspective(image,H,(700,700))
    warped_contour=cv.perspectiveTransform(contour.astype(np.float32),H).astype(np.int32)
    fit=grid_calibration(warped,warped_contour,.2,5)
    assert fit is not None
    transform,info=fit
    rectified=cv.perspectiveTransform(warped_contour.astype(np.float32),transform)
    assert sorted(cv.minAreaRect(rectified)[1])==pytest.approx([100,100],abs=2)
    assert info['intersection_count']>=36
    assert info['lattice_residual_mm']<.75
    fit2=grid_calibration(warped,warped_contour,.2,10)
    assert fit2[1]['assumed_pitch_mm']==10
    assert cv.minAreaRect(cv.perspectiveTransform(warped_contour.astype(np.float32),fit2[0]))[1][0]==pytest.approx(200,abs=4)


def test_no_grid_no_perspective_claim():
    image=scene()
    c=np.int32([[800,180],[1050,180],[1050,530],[800,530]]).reshape(-1,1,2)
    assert grid_calibration(image,c,.2,5) is None


def test_web_multi_html_and_json():
    from io import BytesIO
    from easyon_scan.web import create_app
    client=create_app().test_client()
    _,png=cv.imencode('.png',scene())
    def upload(response=None):
        data=dict(reference='bic',all_objects='true',check_reference='bic-mini',grid_pitch_mm='5',photo=(BytesIO(png.tobytes()),'synthetic.png'))
        if response: data['response']=response
        return client.post('/',data=data)
    reply=upload('json')
    assert reply.status_code==200
    assert len(reply.json['objects'])==3
    reply=upload()
    assert reply.status_code==200
    assert 'objects.json' in reply.get_data(as_text=True)
    assert 'Lados (mm)' in reply.get_data(as_text=True)


def test_thin_reflective_l_and_dark_candidate_flag():
    image=scene()
    # Thin concave metal remains a single component after edge/morphology fusion.
    cv.fillPoly(image,[np.int32([[740,570],[748,570],[748,780],[840,780],[840,788],[740,788]])],(160,160,160))
    cv.rectangle(image,(980,650),(1100,800),(105,105,105),-1)
    result=measure(image,all_objects=True,reference='bic',check_reference='bic-mini')
    assert len(result.objects)==5
    thin=min(result.objects,key=lambda o:o['width_mm'])
    assert len(thin['sides_mm'])==6
    assert max(thin['sides_mm'])==pytest.approx(43.6,abs=1)
    assert any(o['uncertain_segmentation'] for o in result.objects)


def test_plane_homography_shared_check_and_ignored_candidates():
    from easyon_scan.multi import measure_all, table_pt
    image=scene()
    # Grid independent of the J26 check dimensions.
    for x in range(815,1050,25):
        cv.line(image,(x,180),(x,530),(100,100,100),1)
    for y in range(195,530,25):
        cv.line(image,(800,y),(1050,y),(100,100,100),1)
    # Replace second lighter with a strongly foreshortened coloured J26.
    cv.rectangle(image,(245,95),(365,415),(25,25,25),-1)
    cv.rectangle(image,(250,100),(360,270),(210,140,70),-1)
    r=measure_all(image,'bic',check_reference='bic-mini')
    assert r.scale_mm_per_pixel is None
    assert r.check_reference['reference']=='bic'
    assert r.check_reference['measured_length_mm']==pytest.approx(82,abs=2)
    assert r.check_reference['error_estimate_percent']<3
    assert any(o.get('reason')=='fora do plano' for o in r.objects)
    key=next(o for o in r.objects if 'arms' in o)
    assert key['arms']['long_mm']==pytest.approx(95,abs=2)
    assert key['arms']['short_mm']==pytest.approx(42,abs=2)
    assert all('homografia' in o['correction'] for o in r.objects if not o.get('ignored'))
    assert 'longo' in table_pt(r.summary())
    assert 'fora do plano' in table_pt(r.summary())


def test_arm_extents_rotation_invariant():
    from easyon_scan.multi import arm_measurements
    poly=np.float32([[0,0],[5,0],[5,90],[42,90],[42,95],[0,95]])
    c=np.zeros((120,100),np.uint8);cv.fillPoly(c,[poly.astype(np.int32)],255)
    contour=cv.findContours(c,cv.RETR_EXTERNAL,cv.CHAIN_APPROX_NONE)[0][0].reshape(-1,2)
    for angle in (0,.4,1.2):
        rot=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
        arms=arm_measurements((contour@rot.T).astype(np.float32))
        assert arms['long_mm']==pytest.approx(95,abs=.1)
        assert arms['short_mm']==pytest.approx(42,abs=.1)


def test_plane_reference_selection_uses_size_not_just_aspect():
    from easyon_scan.multi import measure_all
    image=scene()
    for x in range(815,1050,25):
        cv.line(image,(x,180),(x,530),(100,100,100),1)
    for y in range(195,530,25):
        cv.line(image,(800,y),(1050,y),(100,100,100),1)
    # A raised projection can coincidentally match the nominal aspect ratio
    # more closely while disagreeing in absolute rectified dimensions.
    cv.rectangle(image,(45,95),(180,515),(25,25,25),-1)
    cv.rectangle(image,(50,100),(166,500),(160,70,40),-1)
    cv.rectangle(image,(245,95),(395,580),(25,25,25),-1)
    cv.rectangle(image,(250,100),(393,569),(210,140,70),-1)
    cv.rectangle(image,(1090,680),(1170,760),(105,105,105),-1)
    r=measure_all(image,'bic')
    flat=next(o for o in r.objects if o['kind']=='BIC J26 deitado')
    assert flat['id']==1
    assert r.check_reference['measured_length_mm']==pytest.approx(80,abs=2)
    assert any(o.get('reason')=='fora do plano' for o in r.objects)
    assert any(o.get('kind')=='fundo' and o['ignored'] for o in r.objects)
    assert len(r.pieces)==2
