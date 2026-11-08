"""Local, stateless upload UI. Images and exports live only in request memory."""
import base64
from io import BytesIO
import cv2 as cv
from flask import Flask, Request, request, render_template_string, jsonify
from PIL import UnidentifiedImageError
from .scan import measure, ScanError, load_photo
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
<p><a href="/capture">Capture with camera</a></p>
<form method="post" enctype="multipart/form-data">
<label>Phone photograph <input type="file" name="photo" accept="image/*" required></label>
<label>Camera profile JSON <input type="file" name="profile" accept=".json"></label>
<label>DXF mode <select name="dxf_mode"><option value="plain">Plain (default)</option><option value="aama">AAMA/ASTM style (experimental)</option></select></label>
<label>Size <input name="size" value="BASE" maxlength="80"></label>
<button>Measure</button></form>
<p>Uploads are processed in memory and are not saved on the server. Maximum 25 MB / 40 MP.</p>
{% if error %}<p class="error">{{error}}</p>{% endif %}
{% for check in checks or [] %}<p>{{check.name}}: {{check.status}} ({{check.value}}). {{check.message}}</p>{% endfor %}
{% if result %}<h2>{{'%.2f'|format(result.width_mm)}} × {{'%.2f'|format(result.height_mm)}} mm</h2>
<p>Pieces: {{result.pieces|length}}. Total net area: {{'%.1f'|format(result.area_mm2)}} mm².
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

    @app.get("/capture")
    def capture():
        return render_template_string(CAPTURE)

    @app.post("/capture/check")
    def capture_check():
        if (request.content_length or 0) > 500_000:
            return jsonify(ready=False, error="Preview frame exceeds 500 KB"), 413
        upload = request.files.get("photo")
        if upload is None:
            return jsonify(ready=False, error="Select a preview frame"), 400
        try:
            image = load_photo(upload.stream)
            if max(image.shape[:2]) > 1280:
                raise ScanError("Preview must be at most 1280 pixels per side")
            rows = measure(image, layout=layout, quality_only=True)
            states = {r["name"]: r for r in rows}
            lights = dict(markers="pass", sharpness=states["focus"]["status"],
                          tilt=states["plane_tilt_proxy"]["status"])
            ready = all(r["status"] != "reject" for r in rows)
            return jsonify(ready=ready, lights=lights, checks=rows)
        except (ScanError, UnidentifiedImageError, OSError, ValueError, ImageDecompressionError, cv.error) as exc:
            return jsonify(ready=False, lights=dict(markers="reject", sharpness="unknown", tilt="unknown"), error=str(exc)), 400

    @app.route("/", methods=["GET", "POST"])
    def index():
        if request.method == "GET":
            return render_template_string(PAGE)
        upload = request.files.get("photo")
        if upload is None:
            return render_template_string(PAGE, error="Select a photograph"), 400
        try:
            import json
            profile = request.files.get("profile")
            result = measure(upload.stream, layout=layout, profile=json.load(profile.stream) if profile and profile.filename else None)
            ok, png = cv.imencode(".png", result.preview)
            if not ok:
                raise ScanError("Could not encode preview")
            return render_template_string(PAGE, result=result, checks=result.quality_checks,
                preview=base64.b64encode(png).decode("ascii"),
                dxf=base64.b64encode(dxf_bytes(result, mode=request.form.get("dxf_mode", "plain"), size=request.form.get("size", "BASE"))).decode("ascii"))
        except (ScanError, UnidentifiedImageError, OSError, ValueError, ImageDecompressionError) as exc:
            return render_template_string(PAGE, error=str(exc), checks=getattr(exc, "checks", [])), 400

    return app


from PIL.Image import DecompressionBombError as ImageDecompressionError


CAPTURE = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>Camera — EasyON Scan</title>
<style>body{font:18px system-ui;max-width:900px;margin:30px auto;padding:16px}video{width:100%;max-height:65vh;background:#222}.pass{color:#087a25}.reject{color:#b00020}.unknown,.warn{color:#946000}button{padding:12px}label{display:block}</style>
<h1>Camera capture</h1><p><a href="/">Upload a photograph</a></p>
<p>Surround the paper with four distinct 90 mm ArUco cards. Preview checks are experimental;
full-resolution measurement runs after capture. A green light does not certify accuracy.</p>
<p>Camera access requires HTTPS or localhost. A phone opening a computer's HTTP LAN address
cannot normally use its camera; use the upload page or configure trusted local HTTPS.
No images are saved. Preview frames go only to this local server.</p>
<button id="start">Start camera</button><button id="stop" disabled>Stop camera</button>
<video id="video" autoplay muted playsinline></video>
<p id="status" role="status" aria-live="polite">Camera stopped</p>
<p id="lights" aria-live="polite">🟡 Markers: unknown · 🟡 Sharpness: unknown · 🟡 Tilt: unknown</p>
<form id="capture" action="/" method="post" enctype="multipart/form-data">
<input id="photo" type="file" name="photo" hidden required>
<label>Camera profile JSON <input type="file" name="profile" accept=".json"></label>
<label>DXF mode <select name="dxf_mode"><option value="plain">Plain (default)</option><option value="aama">AAMA/ASTM style (experimental)</option></select></label>
<label>Size <input name="size" value="BASE" maxlength="80"></label>
<button id="shoot" type="button" disabled>Take photo and measure</button></form>
<script>
const video=document.getElementById('video'), start=document.getElementById('start'),
stop=document.getElementById('stop'), shoot=document.getElementById('shoot'),
status=document.getElementById('status'), lights=document.getElementById('lights');
let stream=null, timer=null, generation=0, lastGood=0;
const canvas=document.createElement('canvas');
function halt(){generation++;clearTimeout(timer);if(stream)stream.getTracks().forEach(t=>t.stop());stream=null;video.srcObject=null;shoot.disabled=true;stop.disabled=true;start.disabled=false;lastGood=0;status.textContent='Camera stopped';lights.textContent='🟡 Markers: unknown · 🟡 Sharpness: unknown · 🟡 Tilt: unknown';}
async function check(token){
 if(!stream||token!==generation)return;
 shoot.disabled=true;
 try{
  if(!video.videoWidth)throw Error('Waiting for video');
  const scale=Math.min(1,1280/Math.max(video.videoWidth,video.videoHeight));
  canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
  canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
  const blob=await new Promise(r=>canvas.toBlob(r,'image/jpeg',0.8));
  const data=new FormData();data.append('photo',blob,'preview.jpg');
  const response=await fetch('/capture/check',{method:'POST',body:data,signal:AbortSignal.timeout(10000)});
  const result=await response.json();if(token!==generation)return;
  const states=result.lights||{markers:'unknown',sharpness:'unknown',tilt:'unknown'};
  lights.textContent=Object.entries(states).map(([k,v])=>(v==='pass'?'🟢':v==='reject'?'🔴':'🟡')+' '+k+': '+v).join(' · ');
  status.textContent=result.error||(result.ready?'Ready to capture':'Adjust camera: '+(result.checks||[]).filter(c=>c.status==='reject').map(c=>c.message).join(' '));
  lastGood=result.ready?Date.now():0;shoot.disabled=!result.ready;
 }catch(e){if(token!==generation)return;lastGood=0;shoot.disabled=true;status.textContent=e.message;lights.textContent='🟡 Preview unavailable';}
 if(token===generation)timer=setTimeout(()=>check(token),1000);
}
start.onclick=async()=>{start.disabled=true;const token=++generation;
 try{if(!navigator.mediaDevices?.getUserMedia)throw Error('Camera unavailable: use HTTPS/localhost or upload a photo');
 const acquired=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:4096},height:{ideal:3072}},audio:false});
 if(token!==generation){acquired.getTracks().forEach(t=>t.stop());return;}
 stream=acquired;stream.getVideoTracks().forEach(t=>t.addEventListener('ended',halt));video.srcObject=stream;await video.play();stop.disabled=false;check(token);
 }catch(e){halt();status.textContent=e.message;}};
stop.onclick=halt;window.addEventListener('pagehide',halt);
document.addEventListener('visibilitychange',()=>{if(document.hidden)halt();});
shoot.onclick=async()=>{
 if(!stream||shoot.disabled||Date.now()-lastGood>2500)return;
 shoot.disabled=true;const token=++generation;clearTimeout(timer);
 try{
 const full=document.createElement('canvas');full.width=video.videoWidth;full.height=video.videoHeight;
 full.getContext('2d').drawImage(video,0,0);
 const blob=await new Promise(r=>full.toBlob(r,'image/jpeg',0.95));
 if(token!==generation)return;
 if(!blob||blob.size>25*1024*1024){halt();status.textContent='Photo too large or unavailable; use upload';return;}
 const files=new DataTransfer();files.items.add(new File([blob],'capture.jpg',{type:'image/jpeg'}));
 document.getElementById('photo').files=files.files;halt();status.textContent='Measuring full-resolution photo…';document.getElementById('capture').submit();
 }catch(e){halt();status.textContent='Capture failed: '+e.message+'. Use photograph upload.';}
};
</script></html>"""
