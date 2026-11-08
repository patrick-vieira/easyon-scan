"""Local UI routes, catalogue handoff and language boundaries."""
from io import BytesIO
from types import SimpleNamespace
import numpy as np
import pytest
import easyon_scan.web as web


@pytest.mark.parametrize('path', ['/', '/capture'])
@pytest.mark.parametrize('lang,html_lang,title', [('pt','pt-BR','Capturar com a câmera'), ('en','en','Capture with camera')])
def test_languages(path, lang, html_lang, title):
    response = web.create_app().test_client().get(path + '?lang=' + lang)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert f'<html lang="{html_lang}">' in html
    assert title in html
    assert '<meta name="viewport"' in html
    assert '<details>' in html
    assert 'https://' not in html
    assert response.headers['Cache-Control'] == 'no-store'


def test_default_fallback_and_translation_parity():
    client = web.create_app().test_client()
    for path in ['/', '/?lang=unknown', '/capture']:
        assert '<html lang="pt-BR">' in client.get(path).get_data(as_text=True)
    assert web.STRINGS['pt'].keys() == web.STRINGS['en'].keys()
    html = client.get('/').get_data(as_text=True)
    for key in ['a4', 'letter', 'card', 'bic', 'phone:iphone-15', 'phone:galaxy-s24']:
        assert f'value="{key}"' in html
    assert 'easyon-scan.pt.vtt' in html and 'easyon-scan.en.vtt' in html
    assert 'easyon-scan-poster.jpg' in html


def test_media_is_local_and_allowlisted():
    client = web.create_app().test_client()
    for name in ['easyon-scan.mp4', 'easyon-scan-poster.jpg', 'easyon-scan.pt.vtt', 'easyon-scan.en.vtt']:
        response = client.get('/media/' + name, headers={'Range':'bytes=0-99'})
        assert response.status_code in (200, 206)
        assert response.data
    assert client.get('/media/README.md').status_code == 404
    assert client.get('/static/style.css').status_code == 200


@pytest.mark.parametrize('lang,expected', [('pt','Selecione uma fotografia.'), ('en','Select a photograph.')])
def test_localized_errors(lang, expected):
    client = web.create_app().test_client()
    response = client.post('/?lang='+lang)
    assert response.status_code == 400
    assert expected in response.get_data(as_text=True)
    response = client.post('/capture/check?lang='+lang)
    assert response.status_code == 400
    assert response.json['error'] == expected
    response = client.post('/?lang='+lang, data={'photo':(BytesIO(b'bad'),'bad.jpg')})
    assert response.status_code == 400
    assert web.STRINGS[lang]['error'] in response.get_data(as_text=True)


def test_result_and_reference_handoff(monkeypatch):
    seen = {}
    result = SimpleNamespace(preview=np.zeros((30,30,3),np.uint8),width_mm=120.,height_mm=200.,area_mm2=24000.,pieces=[1],quality_checks=[],reference='bic',warnings=[])
    def measure(*args, **kwargs):
        seen.update(kwargs)
        return result
    monkeypatch.setattr(web,'measure',measure)
    monkeypatch.setattr(web,'dxf_bytes',lambda *args,**kwargs:b'DXF')
    monkeypatch.setattr(web,'load_photo',lambda stream:np.zeros((30,30,3),np.uint8))
    client = web.create_app().test_client()
    response = client.post('/',data={'lang':'pt','reference':'bic','reference_frontal':'true','photo':(BytesIO(b'photo'),'photo.jpg')})
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert seen['reference'] == 'bic' and seen['reference_frontal'] is True
    assert '120.00 × 200.00' in html and 'Baixar DXF' in html
    assert 'data:application/dxf;base64,RFhG' in html
    assert web.STRINGS['pt']['warning_bic'] in html
    capture = client.get('/capture?lang=en&reference=bic&reference_frontal=true').get_data(as_text=True)
    assert 'name="reference" value="bic"' in capture
    assert 'name="reference_frontal" value="true"' in capture


def test_upload_limit_is_localized():
    app = web.create_app()
    app.config['MAX_CONTENT_LENGTH'] = 100
    response = app.test_client().post('/?lang=en',data={'photo':(BytesIO(b'x'*200),'photo.jpg')})
    assert response.status_code == 413
    assert web.STRINGS['en']['large'] in response.get_data(as_text=True)


def test_reference_preview_and_non_script_form(monkeypatch):
    seen = {}
    def measure(*args, **kwargs):
        seen.update(kwargs)
        if kwargs.get('quality_only'):
            return [dict(name='reference',status='warn',value=None,message='internal')]
        raise web.ScanError('internal')
    monkeypatch.setattr(web,'measure',measure)
    monkeypatch.setattr(web,'load_photo',lambda stream:np.zeros((30,30,3),np.uint8))
    client = web.create_app().test_client()
    response = client.post('/capture/check?lang=en',data={'reference':'a4','photo':(BytesIO(b'photo'),'photo.jpg')})
    assert response.status_code == 200 and response.json['ready']
    assert seen['reference'] == 'a4'
    assert response.json['lights']['sharpness'] == 'unknown'
    assert 'internal' not in response.get_data(as_text=True)
    response = client.post('/',data={'reference_choice':'phone','phone_model':'phone:galaxy-s24','photo':(BytesIO(b'photo'),'photo.jpg')})
    assert response.status_code == 400
    assert seen['reference'] == 'phone:galaxy-s24'
