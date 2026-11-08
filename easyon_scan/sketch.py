"""Experimental sketch tracing. Scale is declared by the user, never inferred units."""
from io import BytesIO
import math
import re
import cv2 as cv
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from .scan import Piece, Measurement, ScanError, load_photo
from .calibration import project


def length_mm(value):
    match = re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?\s*', str(value))
    if not match:
        raise ValueError('Use a positive length such as 10 cm')
    value = float(match[1].replace(',', '.')) * {'mm': 1, 'cm': 10, 'm': 1000, None: 1}[match[2]]
    if not 0 < value <= 10000:
        raise ValueError('Scale length must be between 0 and 10000 mm')
    return value


def thin(mask):
    """Zhang-Suen thinning, preserving stroke connectivity."""
    a = np.pad(mask > 0, 1).astype(np.uint8)
    while True:
        changed = False
        for phase in (0, 1):
            p = [a[:-2,1:-1],a[:-2,2:],a[1:-1,2:],a[2:,2:],a[2:,1:-1],a[2:,:-2],a[1:-1,:-2],a[:-2,:-2]]
            count = sum(p)
            transitions = sum((p[i] == 0) & (p[(i+1)%8] == 1) for i in range(8))
            guard = (p[0]*p[2]*p[4] == 0) & (p[2]*p[4]*p[6] == 0) if phase == 0 else (p[0]*p[2]*p[6] == 0) & (p[0]*p[4]*p[6] == 0)
            remove = (a[1:-1,1:-1] == 1) & (count >= 2) & (count <= 6) & (transitions == 1) & guard
            changed |= bool(remove.any())
            a[1:-1,1:-1][remove] = 0
        if not changed:
            return a[1:-1,1:-1]*255


def paths(mask):
    points = set(map(tuple, np.argwhere(mask)))
    neighbors = {p: sorted(q for dy in (-1,0,1) for dx in (-1,0,1) if (dy or dx) and (q := (p[0]+dy,p[1]+dx)) in points) for p in points}
    used = set(); lines = []
    starts = sorted(points, key=lambda p: (len(neighbors[p]) == 2, p))
    for start in starts:
        for nxt in neighbors[start]:
            if (start,nxt) in used: continue
            line=[start]; prev=start; cur=nxt
            while True:
                used.update(((prev,cur),(cur,prev))); line.append(cur)
                options=[q for q in neighbors[cur] if (cur,q) not in used]
                if len(neighbors[cur]) != 2 or not options: break
                prev,cur=cur,options[0]
            if len(line)>4: lines.append(np.float32(line)[:,::-1])
    return lines


def calibration_axes(image, H, ink, values):
    """Recognize the kit's blue dotted rulers in the rectified grid frame.

    Dark overdraw in the narrow ruler corridor takes precedence over print.
    Written numbers are deliberately supplied by the person, never OCR'd.
    """
    b,g,r=cv.split(image.astype(np.int16))
    yy,xx=np.where((b-r>25)&(b-g>1.3*(g-r))&(r>100))
    if len(xx)<20:
        raise ScanError('Calibration lines missing; include the blue dotted kit rulers')
    metric=project(np.column_stack((xx,yy)),H)
    correction=np.ones(2); report={}
    for axis,name,value in zip((0,1),('x','y'),values):
        if value is None: continue
        value=length_mm(value)
        normal=1-axis
        bins=np.round(metric[:,normal]).astype(int)
        locations,counts=np.unique(bins,return_counts=True)
        eligible=[(count,loc) for loc,count in zip(locations,counts)
                  if np.ptp(metric[bins==loc,axis])>20]
        if not eligible: raise ScanError("Calibration line missing: "+name)
        center=float(max(eligible)[1])
        nearby=metric[np.abs(metric[:,normal]-center)<4]
        center=float(np.median(nearby[:,normal]))
        selected=metric[np.abs(metric[:,normal]-center)<3]
        lo,hi=np.percentile(selected[:,axis],[0,100])
        if hi-lo<20: raise ScanError('Calibration line missing or too short: '+name)
        printed_length=float(hi-lo)
        # Only ink over this ruler is a hand-drawn reference, never sketch ink.
        iy,ix=np.where(ink>0)
        dark=project(np.column_stack((ix,iy)),H)
        inside=(np.abs(dark[:,normal]-center)<1)&(dark[:,axis]>=lo-1)&(dark[:,axis]<=hi+1)
        over=dark[inside]
        hand=False
        if len(over)>20:
            positions=np.sort(over[:,axis])
            groups=np.split(positions,np.where(np.diff(positions)>2)[0]+1)
            runs=[v for v in groups if np.ptp(v)>20]
            if len(runs)>1: raise ScanError('Ambiguous hand-drawn calibration: '+name)
            if runs:
                lo,hi=float(runs[0].min()),float(runs[0].max());hand=True
        observed=float(hi-lo)
        correction[axis]=value/observed
        report[name]=dict(measured_mm=value,detected_length_mm=observed,
                          correction_factor=float(correction[axis]),
                          print_stretch_percent=float((correction[axis]*printed_length/100-1)*100),
                          hand_drawn=hand)
        # Calibration ink must never become pattern geometry.
        erase=(np.abs(dark[:,normal]-center)<3)&(dark[:,axis]>=lo-3)&(dark[:,axis]<=hi+3)
        ink[iy[erase],ix[erase]]=0
    return np.diag([*correction,1.])@H,report


def measure_sketch(source, *, grid_pitch_mm=5., bar_points=None, bar_length=None, calib_mm_x=None, calib_mm_y=None):
    image = source if isinstance(source,np.ndarray) else load_photo(source)
    if not np.isfinite(grid_pitch_mm) or grid_pitch_mm <= 0: raise ValueError('Invalid grid pitch')
    if calib_mm_x is not None or calib_mm_y is not None:
        for value in (calib_mm_x,calib_mm_y):
            if value is not None: length_mm(value)
        b,g,r=cv.split(image.astype(np.int16))
        if np.count_nonzero((b-r>25)&(b-g>1.3*(g-r))&(r>100))<20:
            raise ScanError('Calibration lines missing; include the blue dotted kit rulers')
    gray=cv.cvtColor(image,cv.COLOR_BGR2GRAY)
    # Light/coloured ruling is separated from darker pencil/pen. A dark ruling
    # cannot safely be distinguished from intended straight strokes.
    ink=(gray<140).astype(np.uint8)*255
    # Exclude the dark table surrounding a light sketch sheet.
    paper_contours=cv.findContours((gray>150).astype(np.uint8)*255,cv.RETR_EXTERNAL,cv.CHAIN_APPROX_SIMPLE)[0]
    if paper_contours:
        paper=np.zeros_like(ink)
        cv.drawContours(paper,[max(paper_contours,key=cv.contourArea)],-1,255,-1)
        paper=cv.erode(paper,np.ones((5,5),np.uint8))
        ink=cv.bitwise_and(ink,paper)
    if bar_length and bar_points is None:
        # An isolated map-like bar has a long, thin envelope. Ambiguous bars
        # require explicit endpoints rather than guessing a construction line.
        dark=ink.copy()
        candidates=[]
        for c in cv.findContours(dark,cv.RETR_EXTERNAL,cv.CHAIN_APPROX_SIMPLE)[0]:
            center,dims,angle=cv.minAreaRect(c)
            if min(dims)<=0 or max(dims)<50 or max(dims)/min(dims)<6: continue
            points=c.reshape(-1,2).astype(float)
            _,_,axes=np.linalg.svd(points-points.mean(axis=0),full_matrices=False)
            axis=axes[0];positions=(points-np.array(center))@axis
            endpoints=np.array(center)+np.outer([positions.min(),positions.max()],axis)
            candidates.append(endpoints)
        if len(candidates)!=1:
            raise ScanError('Scale bar missing or ambiguous; supply its two endpoints')
        bar_points=candidates[0]
    if bar_points is not None:
        pts=np.asarray(bar_points,dtype=float).reshape(2,2)
        distance=np.linalg.norm(pts[1]-pts[0])
        if not np.isfinite(pts).all() or distance<10 or (pts<0).any() or (pts[:,0]>=image.shape[1]).any() or (pts[:,1]>=image.shape[0]).any():
            raise ValueError('Select two distinct scale bar endpoints inside the photo')
        scale=length_mm(bar_length)/distance
        H=np.diag([scale,scale,1.])
    else:
        from .multi import grid_calibration
        light=(gray>150).astype(np.uint8)*255
        contours=cv.findContours(light,cv.RETR_EXTERNAL,cv.CHAIN_APPROX_SIMPLE)[0]
        grid_image=image
        b,g,r=cv.split(image.astype(np.int16))
        if np.count_nonzero((b-r>25)&(b-g>1.3*(g-r))&(r>100))>20:
            ruling=(b-r>10)&(b-r<60)&(np.abs((b-g)-(g-r))<8)
            grid_image=np.full_like(image,255)
            grid_image[ruling]=(170,170,170)
        fits=[fit for c in contours if cv.contourArea(c)>1000 and (fit:=grid_calibration(grid_image,c,1.,grid_pitch_mm)) is not None]
        if not fits: raise ScanError('No square grid found; use a scale bar with endpoints')
        H=max(fits,key=lambda f:f[1]['intersection_count'])[0]
    if bar_points is not None:
        cv.line(ink,tuple(pts[0].astype(int)),tuple(pts[1].astype(int)),0,15)
    calibration={}
    if calib_mm_x is not None or calib_mm_y is not None:
        H,calibration=calibration_axes(image,H,ink,(calib_mm_x,calib_mm_y))
    skeleton=thin(ink)
    # Closed strokes enclose background components. Internal open strokes do
    # not partition a region; trace them separately on INTERNAL.
    count,labels,stats,_=cv.connectedComponentsWithStats(255-skeleton,connectivity=4)
    pieces=[]; boundaries=np.zeros_like(ink)
    for ident in range(1,count):
        x,y,w,h,area=stats[ident]
        if x==0 or y==0 or x+w==ink.shape[1] or y+h==ink.shape[0] or area<100: continue
        region=(labels==ident).astype(np.uint8)*255
        c=max(cv.findContours(region,cv.RETR_EXTERNAL,cv.CHAIN_APPROX_SIMPLE)[0],key=cv.contourArea)
        poly=project(cv.approxPolyDP(c,1.,True).reshape(-1,2),H)
        if len(poly)<3: continue
        pieces.append(Piece(poly,[]))
        cv.drawContours(boundaries,[c],-1,255,7)
    if not pieces: raise ScanError('No closed sketch contours found; close gaps in the outline')
    internal=skeleton.copy();internal[boundaries>0]=0
    from shapely.geometry import Point
    for line in paths(internal):
        metric=project(cv.approxPolyDP(line,1.,False).reshape(-1,2),H)
        if len(metric)<2: continue
        for piece in pieces:
            if piece.polygon.contains(Point(metric.mean(axis=0))):
                piece.internal_lines_mm.append(metric);break
    allpoints=np.concatenate([p.contour_mm for p in pieces]);lo=allpoints.min(axis=0);hi=allpoints.max(axis=0)
    if max(hi-lo)>10000: raise ScanError('Sketch exceeds 10 m; verify scale')
    preview=image.copy()
    for i,p in enumerate(pieces,1):
        contour=project(p.contour_mm,np.linalg.inv(H)).astype(np.int32)
        cv.polylines(preview,[contour],True,(0,0,255),2)
        dims=np.ptp(p.contour_mm,axis=0)
        cv.putText(preview,f'{i}: {dims[0]:.1f} x {dims[1]:.1f} mm',tuple(contour[0]),cv.FONT_HERSHEY_SIMPLEX,.6,(0,0,255),2)
    result=Measurement(pieces[0].contour_mm,sum(p.polygon.area for p in pieces),float(hi[0]-lo[0]),float(hi[1]-lo[1]),None,None,preview,[],pieces,{},'sketch','experimental-sketch',['Experimental tracing; inspect every contour and marking. No real-photo accuracy claim. Scale bars assume a frontal, flat photograph.'])
    result.calibration=calibration
    return result


def tiled_pdf(result):
    """Raster PDF at 254 dpi, A4, 10 mm margins and 10 mm overlap."""
    points=np.concatenate([p.contour_mm for p in result.pieces]);origin=points.min(axis=0)
    extent=points.max(axis=0)-origin
    cols=max(1,math.ceil(max(0,extent[0]-10)/180));rows=max(1,math.ceil(max(0,extent[1]-10)/267))
    if cols*rows>100: raise ValueError('PDF limited to 100 A4 pages')
    pages=[]
    for row in range(rows):
        for col in range(cols):
            page=Image.new('RGB',(2100,2970),'white');draw=ImageDraw.Draw(page)
            offset=origin+np.array([col*180,row*267])
            for p in result.pieces:
                for line,closed in [(p.contour_mm,True)]+[(v,False) for v in p.internal_lines_mm]:
                    xy=[tuple(v) for v in ((line-offset+10)*10)]
                    draw.line(xy+([xy[0]] if closed else []),fill='black',width=3)
            # White margins clip geometry outside the printable viewport.
            for box in [(0,0,2100,99),(0,2871,2100,2970),(0,0,99,2970),(2001,0,2100,2970)]:draw.rectangle(box,fill='white')
            for x in (100,1900):
                for y in (100,2770):
                    draw.line((x-25,y,x+25,y),fill='gray',width=2);draw.line((x,y-25,x,y+25),fill='gray',width=2)
            draw.text((110,30),f'A4 {row+1},{col+1} / {rows},{cols} | 100% - 1:1 | overlap 10 mm',fill='black',font=ImageFont.load_default(size=26))
            pages.append(page)
    out=BytesIO();pages[0].save(out,'PDF',resolution=254.,save_all=True,append_images=pages[1:]);return out.getvalue()


def sketch_kit_image():
    """A4 at 10 pixels/mm; reserve side corridors outside the drawing grid."""
    page=Image.new('RGB',(2100,2970),'white');d=ImageDraw.Draw(page)
    font=ImageFont.load_default(size=25)
    d.text((100,60),'SKETCH KIT | 5 mm | Calibrar com regua / Calibrate with ruler',fill=(155,155,155),font=font)
    for x in range(100,1751,50): d.line((x,200,x,2400),fill=(170,190,210),width=2)
    for y in range(200,2401,50): d.line((100,y,1750,y),fill=(170,190,210),width=2)
    color=(150,180,230)
    for axis,start in [(0,(200,2650)),(1,(1900,400))]:
        x,y=start
        for t in range(0,1000,20):
            d.line((x+(t if axis==0 else 0),y+(t if axis==1 else 0),
                    x+(t+8 if axis==0 else 0),y+(t+8 if axis==1 else 0)),fill=color,width=3)
        for t,label in [(0,'0'),(1000,'10 cm')]:
            px=x+(t if axis==0 else 0);py=y+(t if axis==1 else 0)
            d.line((px-(0 if axis==0 else 20),py-(20 if axis==0 else 0),
                    px+(0 if axis==0 else 20),py+(20 if axis==0 else 0)),fill=color,width=3)
            d.text((px-20,py+25),label,fill=(155,155,155),font=font)
        bx,by=(200,2770) if axis==0 else (1765,1500)
        d.rectangle((bx,by,bx+(700 if axis==0 else 300),by+120),outline=(180,180,180),width=2)
        d.text((bx+5,by+10),'medido:' if axis else 'medido: ____ mm / measured',fill=(155,155,155),font=font)
        if axis:d.text((bx+5,by+55),'____ mm',fill=(155,155,155),font=font)
    d.text((100,2460),'X / Y: medir pontilhado ou tracar comprimento conhecido com regua.',fill=(155,155,155),font=font)
    d.text((100,2510),'Measure dotted lines or draw known lengths over them. Enter X / Y in mm.',fill=(155,155,155),font=font)
    return page


def sketch_kit(out):
    sketch_kit_image().save(out,'PDF',resolution=254.)


def real_size_svg(result):
    """Metric vector preview; CSS millimetres depend on display calibration."""
    points=np.concatenate([p.contour_mm for p in result.pieces]);origin=points.min(axis=0)-5
    width,height=np.ptp(points,axis=0)+10
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="1:1 mm" width="{width}mm" height="{height}mm" viewBox="0 0 {width} {height}">']
    for p in result.pieces:
        for line,closed in [(p.contour_mm,True)]+[(v,False) for v in p.internal_lines_mm]:
            coords=' '.join(f'{x:.4f},{y:.4f}' for x,y in line-origin)
            tag='polygon' if closed else 'polyline'
            parts.append(f'<{tag} points="{coords}" fill="none" stroke="{"black" if closed else "blue"}" stroke-width="0.3"/>')
    parts.append('</svg>');return ''.join(parts)
