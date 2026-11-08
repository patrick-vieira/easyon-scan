import cv2 as cv
import numpy as np
import pytest
from easyon_scan.markers import dictionary, sheet
from easyon_scan.scan import measure, ScanError
from easyon_scan.stitch import stitch
from easyon_scan.layout import load_layout, marker_sizes


def scene(columns=3):
    image = np.full((1800, (columns-1)*800+400, 3),55,np.uint8)
    sizes = {}
    for col in range(columns):
        for row, y in enumerate((60,1540)):
            ident = col*2+row
            size = 90 if ident%2 == 0 else 60
            sizes[ident] = size
            x = 80+col*800
            image[y-12:y+size*2+12,x-12:x+size*2+12] = 255
            image[y:y+size*2,x:x+size*2] = cv.cvtColor(cv.aruco.generateImageMarker(dictionary(),ident,size*2),cv.COLOR_GRAY2BGR)
    image[500:1300,400:(columns-1)*800] = 240
    return image, sizes


def test_mixed_sizes_saved_layout(tmp_path):
    image, sizes = scene()
    path = tmp_path/'layout.json'
    result = measure(image, marker_size=sizes, save_layout=path)
    assert abs(result.width_mm-400) < 1
    assert abs(result.height_mm-600) < 1
    saved = load_layout(path)
    assert len(saved) == 6
    assert np.linalg.norm(saved[1][1]-saved[1][0]) == pytest.approx(60)
    repeated = measure(image, layout_file=path)
    assert repeated.width_mm == pytest.approx(result.width_mm, abs=.1)
    sheet(tmp_path/'mixed.pdf', marker_size='0=90,1=60')
    assert (tmp_path/'mixed.pdf').read_bytes().startswith(b'%PDF')


@pytest.mark.parametrize('count',[2,3])
def test_split_pattern(count,tmp_path):
    image, sizes = scene(count+1)
    photos = [image[:,i*800:(i+1)*800+400].copy() for i in range(count)]
    # Different projective camera frames, with black margins outside the photos.
    for i in range(1,count):
        h,w = photos[i].shape[:2]
        matrix = cv.getPerspectiveTransform(np.float32([[0,0],[w,0],[w,h],[0,h]]),np.float32([[30,25],[w-20,10],[w-5,h-30],[10,h-5]]))
        photos[i] = cv.warpPerspective(photos[i],matrix,(w,h),borderValue=(55,)*3)
    result = stitch(photos, marker_size=sizes, save_layout=tmp_path/'layout.json')
    assert len(result.pieces) == 1
    assert abs(result.width_mm-400) < 1
    assert abs(result.height_mm-(count*400-200)) < 1
    assert result.residual_mm < .5
    assert result.area_mm2 == pytest.approx(400*(count*400-200), rel=.005)
    again = stitch(photos, layout_file=tmp_path/'layout.json')
    assert again.width_mm == pytest.approx(result.width_mm,abs=.1)


def test_disconnected():
    image,sizes = scene(4)
    with pytest.raises(ScanError,match='connected'):
        stitch([image[:,:1200],image[:,1600:]],marker_size=sizes)


@pytest.mark.parametrize('value',['0=0','50=90','1=nan','oops'])
def test_bad_sizes(value):
    with pytest.raises(ValueError):
        marker_sizes(value)


def test_only_one_shared_marker():
    image,sizes = scene()
    first, second = image[:,:1200].copy(), image[:,800:].copy()
    second[40:260,60:300] = 55  # Remove ID 2; ID 3 is the sole shared ID.
    with pytest.raises(ScanError,match='connected'):
        stitch([first,second],marker_size=sizes)


def test_hole_crosses_photo_boundary():
    image,sizes = scene()
    image[750:950,1050:1350] = 55
    result = stitch([image[:,:1200],image[:,800:]],marker_size=sizes)
    assert len(result.pieces) == 1
    assert len(result.pieces[0].holes_mm) == 1
    assert result.area_mm2 == pytest.approx(240000-15000,rel=.005)


def test_moved_shared_marker_rejected():
    image,sizes = scene()
    first,second = image[:,:1200].copy(),image[:,800:].copy()
    card = second[40:260,60:300].copy()
    second[40:260,60:300] = 55
    second[40:260,260:500] = card
    with pytest.raises(ScanError,match='quality|residual'):
        stitch([first,second],marker_size=sizes)


def test_cli_stitch(tmp_path,monkeypatch,capsys):
    import sys,json
    from easyon_scan.cli import main
    image,sizes = scene()
    paths = [tmp_path/'a.png',tmp_path/'b.png']
    for path,photo in zip(paths,[image[:,:1200],image[:,800:]]):
        cv.imwrite(str(path),photo)
    monkeypatch.setattr(sys,'argv',['easyon-scan','measure',*map(str,paths),'--marker-size',','.join(f'{k}={v}' for k,v in sizes.items()),'--out',str(tmp_path/'out.dxf'),'--save-layout',str(tmp_path/'layout.json')])
    main()
    assert json.loads(capsys.readouterr().out)['piece_count'] == 1
    assert (tmp_path/'out.dxf').exists()
    assert len(load_layout(tmp_path/'layout.json')) == 6
