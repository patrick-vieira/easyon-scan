"""Local, stateless UI. Uploads and exports remain in request memory."""
import base64
from io import BytesIO
from pathlib import Path
import cv2 as cv
from flask import Flask, Request, request, render_template, jsonify, send_from_directory
from werkzeug.datastructures import ImmutableMultiDict
from PIL import UnidentifiedImageError
from PIL.Image import DecompressionBombError as ImageDecompressionError
from .scan import measure, ScanError, load_photo
from .dxf import dxf_bytes
from .references import CATALOGUE

STRINGS = {'pt': {'lang': 'pt-BR',
        'language': 'Idioma',
        'skip': 'Ir para o conteúdo',
        'tagline': 'Do papel ao molde digital',
        'intro': 'Prepare a mesa, fotografe e baixe seu molde em DXF.',
        'reference': 'Escolha a referência',
        'reference_tip': 'Sem marcadores? Coloque o objeto à esquerda, em pé, separado do molde '
                         'por um espaço escuro. Todos devem estar no mesmo plano. Confira as '
                         'dimensões com uma régua.',
        'aruco': 'Marcadores ArUco',
        'aruco_tip': 'Melhor opção · precisão em fotos reais ainda não validada. Use pelo menos '
                     'quatro marcadores distintos de 90 mm.',
        'a4': 'Folha A4',
        'letter': 'Folha Letter',
        'card': 'Cartão bancário',
        'phone': 'Celular sem capa',
        'bic': 'Isqueiro BIC J26',
        'sheet_accuracy': 'Estimativa experimental · confira o tamanho da folha.',
        'small_accuracy': 'Estimativa experimental · o erro aumenta longe da referência.',
        'bic_accuracy': 'Apenas escala aproximada · sem correção de perspectiva.',
        'model': 'Modelo do celular',
        'frontal': 'Confirmo que a foto foi feita de frente, com a câmera diretamente acima '
                   '(obrigatório para BIC).',
        'tips': 'Prepare uma boa foto',
        'tips_text': 'Mesa escura, papel claro e bem plano. Fotografe diretamente de cima, com luz '
                     'uniforme, sem sombras ou reflexos. Deixe todas as bordas visíveis.',
        'fixed': 'Layout fixo: use os marcadores 0–3 na montagem de 780 × 980 mm.',
        'upload': 'Envie sua foto',
        'photo': 'Fotografia do molde',
        'capture': 'Capturar com a câmera',
        'measure': 'Medir molde',
        'privacy': 'As imagens são processadas na memória e não são salvas no servidor. Limite: 25 '
                   'MB / 40 MP.',
        'advanced': 'Opções avançadas',
        'profile': 'Perfil da câmera (JSON)',
        'mode': 'Formato DXF',
        'plain': 'Padrão',
        'aama': 'AAMA/ASTM (experimental)',
        'size': 'Tamanho',
        'result': 'Seu molde digital',
        'preview': 'Prévia retificada com contornos medidos em vermelho',
        'pieces': 'Peças',
        'area': 'Área líquida total',
        'download': 'Baixar DXF',
        'warnings': 'Antes de usar o molde',
        'verify': 'Confira as medidas com uma régua antes de cortar. A precisão em fotos reais '
                  'ainda não foi validada.',
        'how': 'Como funciona',
        'video_fallback': 'Baixar vídeo explicativo',
        'pt_captions': 'Português (Brasil)',
        'en_captions': 'Inglês',
        'back': 'Voltar ao envio de foto',
        'camera_help': 'A câmera precisa de HTTPS ou localhost. No celular, um endereço HTTP da '
                       'rede local geralmente não permite acesso: use o envio de foto ou HTTPS '
                       'local confiável. As prévias vão apenas para este servidor local.',
        'camera_caution': 'As verificações da prévia são experimentais. Um resultado aprovado não '
                          'garante precisão; a medição usa a foto completa.',
        'start': 'Iniciar câmera',
        'stop': 'Parar câmera',
        'shoot': 'Fotografar e medir',
        'stopped': 'Câmera parada',
        'waiting': 'Aguardando prévia da câmera',
        'ready': 'Pronto para fotografar',
        'adjust': 'Ajuste a câmera. ',
        'adjust_tip': 'Mantenha o celular firme, de cima, com a referência inteira visível.',
        'unavailable': 'Prévia indisponível',
        'camera_error': 'Não foi possível usar a câmera. Verifique a permissão e HTTPS/localhost '
                        'ou envie uma foto.',
        'photo_error': 'Foto grande demais ou indisponível. Use o envio de foto.',
        'measuring': 'Medindo a foto em resolução completa…',
        'markers': 'Referência',
        'sharpness': 'Nitidez',
        'tilt': 'Inclinação',
        'pass': 'Bom',
        'reject': 'Ajustar',
        'warn': 'Atenção',
        'unknown': 'Aguardando',
        'missing': 'Selecione uma fotografia.',
        'large': 'O envio ultrapassa 25 MB. Escolha uma foto menor.',
        'error': 'Não foi possível medir. Confira a referência, deixe o papel separado sobre uma '
                 'mesa escura e tente uma foto mais nítida, diretamente de cima. Verifique também '
                 'o perfil JSON, se enviado.',
        'warning_reference': 'Referência experimental: mantenha objeto e molde planos. Objetos '
                             'pequenos e distantes podem aumentar o erro.',
        'warning_lens': 'Sem perfil da câmera, a lente pode distorcer as medidas. Evite a lente '
                        'ultrawide.',
        'warning_scale': 'Confira com régua o tamanho impresso de todos os marcadores; a foto não '
                         'detecta uma impressão fora de escala.',
        'warning_quality': 'Revise a luz, o foco e a posição da câmera. Sombras, reflexos e '
                           'inclinação podem alterar as medidas.',
        'warning_phone': 'Retire a capa e use o contorno do corpo, não o da tela. A tela deve '
                         'ficar branca até as bordas; telas curvas podem impedir a detecção.',
        'warning_bic': 'BIC: escala apenas aproximada, sem correção de perspectiva. Use uma foto '
                       'diretamente de cima.',
        'preview_large': 'A prévia deve ter até 500 KB e 1280 pixels por lado.'},
 'en': {'lang': 'en',
        'language': 'Language',
        'skip': 'Skip to content',
        'tagline': 'From paper to digital pattern',
        'intro': 'Prepare your table, take a photo and download your DXF pattern.',
        'reference': 'Choose a reference',
        'reference_tip': 'No markers? Place the object on the left, in portrait orientation, with '
                         'a dark gap before the pattern. Keep everything on the same plane. Verify '
                         'dimensions with a ruler.',
        'aruco': 'ArUco markers',
        'aruco_tip': 'Best option · real-photo accuracy is not yet validated. Use at least four '
                     'distinct 90 mm markers.',
        'a4': 'A4 sheet',
        'letter': 'Letter sheet',
        'card': 'Bank card',
        'phone': 'Phone without case',
        'bic': 'BIC J26 lighter',
        'sheet_accuracy': 'Experimental estimate · verify sheet dimensions.',
        'small_accuracy': 'Experimental estimate · error increases farther from the reference.',
        'bic_accuracy': 'Approximate scale only · no perspective correction.',
        'model': 'Phone model',
        'frontal': 'I confirm the photo is frontal, with the camera directly overhead (required '
                   'for BIC).',
        'tips': 'Prepare a good photo',
        'tips_text': 'Dark table, light paper lying flat. Photograph directly overhead with even '
                     'light, no shadows or glare. Keep all edges visible.',
        'fixed': 'Fixed layout: use markers 0–3 in the assembled 780 × 980 mm layout.',
        'upload': 'Upload your photo',
        'photo': 'Pattern photograph',
        'capture': 'Capture with camera',
        'measure': 'Measure pattern',
        'privacy': 'Images are processed in memory and are not saved on the server. Limit: 25 MB / '
                   '40 MP.',
        'advanced': 'Advanced options',
        'profile': 'Camera profile (JSON)',
        'mode': 'DXF mode',
        'plain': 'Plain',
        'aama': 'AAMA/ASTM (experimental)',
        'size': 'Size',
        'result': 'Your digital pattern',
        'preview': 'Rectified preview with measured outlines in red',
        'pieces': 'Pieces',
        'area': 'Total net area',
        'download': 'Download DXF',
        'warnings': 'Before using the pattern',
        'verify': 'Check dimensions with a ruler before cutting. Real-photo accuracy has not yet '
                  'been validated.',
        'how': 'How it works',
        'video_fallback': 'Download explainer video',
        'pt_captions': 'Portuguese (Brazil)',
        'en_captions': 'English',
        'back': 'Back to photo upload',
        'camera_help': 'Camera access requires HTTPS or localhost. On a phone, a local network '
                       'HTTP address usually blocks camera access: use photo upload or trusted '
                       'local HTTPS. Previews go only to this local server.',
        'camera_caution': 'Preview checks are experimental. Passing does not guarantee accuracy; '
                          'measurement uses the full photo.',
        'start': 'Start camera',
        'stop': 'Stop camera',
        'shoot': 'Take photo and measure',
        'stopped': 'Camera stopped',
        'waiting': 'Waiting for camera preview',
        'ready': 'Ready to capture',
        'adjust': 'Adjust the camera. ',
        'adjust_tip': 'Hold steady, directly overhead, with the entire reference visible.',
        'unavailable': 'Preview unavailable',
        'camera_error': 'Could not use the camera. Check permissions and HTTPS/localhost or upload '
                        'a photo.',
        'photo_error': 'Photo too large or unavailable. Use photo upload.',
        'measuring': 'Measuring full-resolution photo…',
        'markers': 'Reference',
        'sharpness': 'Sharpness',
        'tilt': 'Tilt',
        'pass': 'Good',
        'reject': 'Adjust',
        'warn': 'Caution',
        'unknown': 'Waiting',
        'missing': 'Select a photograph.',
        'large': 'Upload exceeds 25 MB. Choose a smaller photo.',
        'error': 'Could not measure. Check the reference, separate the paper on a dark table and '
                 'try a sharper photo directly overhead. Also check the JSON profile if supplied.',
        'warning_reference': 'Experimental reference: keep object and pattern flat. Small and '
                             'distant objects can increase error.',
        'warning_lens': 'Without a camera profile, lens distortion can affect dimensions. Avoid '
                        'ultrawide lenses.',
        'warning_scale': 'Check the printed size of every marker with a ruler; a photo cannot '
                         'detect uniform print scaling.',
        'warning_quality': 'Review lighting, focus and camera position. Shadows, glare and tilt '
                           'can affect measurements.',
        'warning_phone': 'Remove the case and use the body outline, not the screen. Show white to '
                         'the body edges; curved screens may prevent detection.',
        'warning_bic': 'BIC: approximate scale only, without perspective correction. Use a photo '
                       'directly overhead.',
        'preview_large': 'Preview must be at most 500 KB and 1280 pixels per side.'}}

for language, messages in {
    'pt': {'reference_border': 'A referência toca a borda da imagem. Fotografe o objeto inteiro.',
           'reference_missing': 'Nenhum objeto com o formato da referência foi encontrado.',
           'reference_ambiguous': 'Há dois candidatos semelhantes à referência. Retire o objeto extra.',
           'pattern_missing': 'Molde não encontrado.', 'pattern_border': 'O molde toca a borda da imagem.',
           'choose_photo': 'Escolher fotografia', 'diagnostic': 'Diagnóstico: candidatos em amarelo e referência em verde'},
    'en': {'reference_border': 'Reference touches the image border. Photograph the entire object.',
           'reference_missing': 'No object with the reference shape found.',
           'reference_ambiguous': 'Two similar reference candidates. Remove the extra object.',
           'pattern_missing': 'Pattern not found.', 'pattern_border': 'Pattern touches the image border.',
           'choose_photo': 'Choose photograph', 'diagnostic': 'Diagnostic: candidates in yellow and reference in green'}
}.items():
    STRINGS[language].update(messages)
STRINGS['pt']['reference_tip'] = 'Separe a referência e o molde sobre a mesa, com todas as bordas visíveis. Qualquer posição ou orientação. Mantenha tudo no mesmo plano e confira as dimensões com uma régua.'
STRINGS['en']['reference_tip'] = 'Separate reference and pattern on the table with all edges visible, in any position or orientation. Keep everything coplanar and verify dimensions with a ruler.'


def diagnostic_error(exc):
    overlay = getattr(exc, 'overlay', None)
    encoded = None
    if overlay is not None:
        scale = min(1., 1200/max(overlay.shape[:2]))
        overlay = cv.resize(overlay, None, fx=scale, fy=scale)
        ok, png = cv.imencode('.png', overlay)
        if ok:
            encoded = base64.b64encode(png).decode('ascii')
    return dict(error=tr(getattr(exc, 'code', 'error')), overlay=encoded,
                checks=getattr(exc, 'checks', []))

class MemoryRequest(Request):
    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        return BytesIO()


def chosen_reference():
    choice = request.values.get("reference_choice")
    if choice is None:
        return request.values.get("reference", "")
    return request.values.get("phone_model", "phone:iphone-15") if choice == "phone" else choice


def tr(key):
    return STRINGS.get(request.values.get("lang"), STRINGS["pt"])[key]


def warnings_for(result):
    keys = ["verify"]
    if result.reference == "sketch":
        return [tr("verify"), "Confira cada contorno e marcação; malha clara e traços escuros. Barra requer foto frontal." if request.values.get("lang", "pt") != "en" else "Inspect every contour and marking; use light ruling and dark strokes. Bars require a frontal photo."]
    if result.reference and result.reference != "aruco":
        keys.append("warning_reference")
    if result.reference == "bic":
        keys.append("warning_bic")
    if result.reference and result.reference.startswith("phone:"):
        keys.append("warning_phone")
    for row in result.quality_checks or []:
        if row["status"] != "pass":
            key = {"lens_profile": "warning_lens", "scale_verification": "warning_scale"}.get(row["name"], "warning_quality")
            if key not in keys:
                keys.append(key)
    return [tr(key) for key in keys]


def create_app(layout="free"):
    app = Flask(__name__)
    app.request_class = MemoryRequest
    app.config.update(MAX_CONTENT_LENGTH=25*1024*1024, MAX_FORM_MEMORY_SIZE=25*1024*1024)

    @app.context_processor
    def layout_context():
        lang = request.values.get("lang", "pt")
        lang = lang if lang in STRINGS else "pt"
        selected = chosen_reference()
        if selected not in CATALOGUE:
            selected = ""
        return dict(layout=layout, lang=lang, t=STRINGS[lang], selected=selected,
                    frontal=request.values.get("reference_frontal") == "true",
                    catalogue=CATALOGUE, warnings_for=warnings_for)

    @app.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(413)
    def too_large(error):
        # Never parse an oversized body again while rendering its error page.
        request.form = ImmutableMultiDict()
        request.files = ImmutableMultiDict()
        if request.endpoint == "capture_check":
            return jsonify(ready=False, error=tr("preview_large")), 413
        return render_template("index.html", error=tr("large")), 413

    @app.get("/media/<name>")
    def media(name):
        if name not in {"easyon-scan.mp4", "easyon-scan-poster.jpg", "easyon-scan.pt.vtt", "easyon-scan.en.vtt"}:
            from flask import abort
            abort(404)
        return send_from_directory(Path(__file__).resolve().parent.parent / "docs", name)

    @app.get("/capture")
    def capture():
        return render_template("capture.html")

    @app.post("/capture/check")
    def capture_check():
        if (request.content_length or 0) > 500_000:
            return jsonify(ready=False, error=tr("preview_large")), 413
        upload = request.files.get("photo")
        if upload is None:
            return jsonify(ready=False, error=tr("missing")), 400
        try:
            image = load_photo(upload.stream)
            if max(image.shape[:2]) > 1280:
                raise ScanError("Preview must be at most 1280 pixels per side")
            rows = measure(image, layout=layout, quality_only=True, reference=chosen_reference() or None, reference_frontal=request.form.get("reference_frontal") == "true")
            states = {r["name"]: r for r in rows}
            lights = dict(markers="pass", sharpness=states.get("focus", {"status": "unknown"})["status"],
                          tilt=states.get("plane_tilt_proxy", {"status": "unknown"})["status"])
            ready = all(r["status"] != "reject" for r in rows)
            return jsonify(ready=ready, lights=lights, checks=[dict(row, message=tr("adjust_tip")) for row in rows])
        except (ScanError, UnidentifiedImageError, OSError, ValueError, ImageDecompressionError, cv.error) as exc:
            return jsonify(ready=False, lights=dict(markers="reject", sharpness="unknown", tilt="unknown"), error=tr(getattr(exc, "code", "error"))), 400

    @app.route("/", methods=["GET", "POST"])
    def index():
        if request.method == "GET":
            return render_template("index.html")
        upload = request.files.get("photo")
        if upload is None:
            return render_template("index.html", error=tr("missing")), 400
        try:
            import json
            profile = request.files.get("profile")
            mode = request.form.get("workflow", "auto")
            if mode not in ("auto", "legacy", "sketch"):
                raise ValueError("Unknown workflow")
            source = load_photo(upload.stream)
            automatic = False
            if mode == "auto" and layout == "free":
                from .multi import segments, grid_calibration
                automatic = any(grid_calibration(source, c, 1., float(request.form.get("grid_pitch_mm", 5))) is not None for c in segments(source))
            if mode == "sketch":
                from .sketch import measure_sketch
                points = request.form.get("bar_points", "").strip()
                result = measure_sketch(source, grid_pitch_mm=float(request.form.get("grid_pitch_mm", 5)), bar_points=json.loads(points) if points else None, bar_length=request.form.get("bar_length"), calib_mm_x=request.form.get("calib_mm_x") or None, calib_mm_y=request.form.get("calib_mm_y") or None)
            else:
                result = measure(source, layout=layout, profile=json.load(profile.stream) if profile and profile.filename else None, reference=(chosen_reference() or "bic") if automatic else (chosen_reference() or None), reference_frontal=request.form.get("reference_frontal") == "true", all_objects=automatic or request.form.get("all_objects") == "true", check_reference=request.form.get("check_reference") or None, grid_pitch_mm=float(request.form.get("grid_pitch_mm", 5)))
            if request.form.get("response") == "json":
                return jsonify(result.summary())
            ok, png = cv.imencode(".png", result.preview)
            if not ok:
                raise ScanError("Could not encode preview")
            report_table = None
            if hasattr(result, "objects"):
                from .multi import table_pt
                report_table = base64.b64encode(table_pt(result.summary()).encode("utf-8")).decode("ascii")
            sketch_pdf = None
            metric_preview = None
            if mode == "sketch":
                from .sketch import tiled_pdf, real_size_svg
                metric_preview = real_size_svg(result)
                sketch_pdf = base64.b64encode(tiled_pdf(result)).decode("ascii")
            return render_template("index.html", result=result, checks=result.quality_checks,
                preview=base64.b64encode(png).decode("ascii"),
                sketch_pdf=sketch_pdf, report_table=report_table,
                metric_preview=metric_preview,
                report_json=base64.b64encode(json.dumps(result.summary(),ensure_ascii=False).encode()).decode("ascii") if hasattr(result, "objects") else None,
                dxf=base64.b64encode(dxf_bytes(result, mode=request.form.get("dxf_mode", "plain"), size=request.form.get("size", "BASE"))).decode("ascii"))
        except (ScanError, UnidentifiedImageError, OSError, ValueError, ImageDecompressionError, cv.error) as exc:
            diagnostic = diagnostic_error(exc)
            if request.form.get("workflow") == "sketch":
                diagnostic["error"] = ("Não foi possível vetorizar. Use malha clara, traços escuros fechados e confira a escala. Se a barra for ambígua, informe seus dois extremos." if request.values.get("lang", "pt") != "en" else "Could not trace the sketch. Use light ruling, dark closed strokes and check the scale. If the bar is ambiguous, enter its two endpoints.")
            return render_template("index.html", **diagnostic), 400

    return app

