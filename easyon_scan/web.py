"""Local, stateless upload UI. Images and exports live only in request memory."""
import base64
from io import BytesIO
import cv2 as cv
from flask import Flask, Request, request, render_template_string
from PIL import UnidentifiedImageError
from .scan import measure, ScanError
from .dxf import dxf_bytes


class MemoryRequest(Request):
    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        return BytesIO()


PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>EasyON Scan</title><style>
body{font:18px system-ui;max-width:900px;margin:40px auto;padding:0 20px;background:#f4f5f6}
button,a{padding:10px}img{width:100%;height:auto} .error{color:#a00}
</style><h1>EasyON Scan</h1>
<p>Place light paper on a dark table,
{% if layout == "fixed" %}inside the assembled 780 × 980 mm four-marker layout.
{% else %}surrounded by four or more freely placed 90 mm markers.{% endif %}
Keep the paper flat and all markers visible. Real photo accuracy is not yet validated.</p>
<form method="post" enctype="multipart/form-data">
<label>Phone photograph <input type="file" name="photo" accept="image/*" required></label>
<button>Measure</button></form>
<p>Uploads are processed in memory and are not saved on the server. Maximum 25 MB / 40 MP.</p>
{% if error %}<p class="error">{{error}}</p>{% endif %}
{% if result %}<h2>{{'%.2f'|format(result.width_mm)}} × {{'%.2f'|format(result.height_mm)}} mm</h2>
<p>Area: {{'%.1f'|format(result.area_mm2)}} mm².
Marker residual: {{'%.2f'|format(result.residual_mm)}} mm.</p>
{% if result.residual_mm > 1 %}<p class="error">Marker residual exceeds 1 mm. Inspect placement and photo.</p>{% endif %}
<img alt="Rectified table with red measured outline" src="data:image/png;base64,{{preview}}">
<p><a download="pattern.dxf" href="data:application/dxf;base64,{{dxf}}">Download DXF</a></p>
{% endif %}</html>"""


def create_app(layout="free"):
    app = Flask(__name__)
    app.request_class = MemoryRequest
    app.config.update(MAX_CONTENT_LENGTH=25*1024*1024, MAX_FORM_MEMORY_SIZE=25*1024*1024)

    @app.context_processor
    def layout_context():
        return {"layout": layout}

    @app.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(413)
    def too_large(error):
        return render_template_string(PAGE, error="Upload exceeds 25 MB"), 413

    @app.route("/", methods=["GET", "POST"])
    def index():
        if request.method == "GET":
            return render_template_string(PAGE)
        upload = request.files.get("photo")
        if upload is None:
            return render_template_string(PAGE, error="Select a photograph"), 400
        try:
            result = measure(upload.stream, layout=layout)
            ok, png = cv.imencode(".png", result.preview)
            if not ok:
                raise ScanError("Could not encode preview")
            return render_template_string(PAGE, result=result,
                preview=base64.b64encode(png).decode("ascii"),
                dxf=base64.b64encode(dxf_bytes(result.contour_mm)).decode("ascii"))
        except (ScanError, UnidentifiedImageError, OSError, ImageDecompressionError) as exc:
            return render_template_string(PAGE, error=str(exc)), 400

    return app


from PIL.Image import DecompressionBombError as ImageDecompressionError
