"""Experimental multi-object silhouettes and local square-grid calibration.

No reference silhouette supplies perspective. A detected grid calibrates the table plane.
"""
import cv2 as cv
import numpy as np
from .scan import Measurement, Piece, ScanError
from .references import foreground, get_reference
from .calibration import project


def segments(image):
    mask = foreground(image)
    gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
    saturation=cv.cvtColor(image,cv.COLOR_BGR2HSV)[:,:,1]
    # Local gradients recover thin reflective metal missed by colour segmentation.
    edges = cv.Canny(cv.GaussianBlur(gray, (3, 3), 0), 35, 100)
    edges = cv.morphologyEx(edges, cv.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    cs, _ = cv.findContours(edges, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
    for c in cs:
        area = cv.contourArea(c)
        x,y,w,h = cv.boundingRect(c)
        if 200 < area < mask.size*.25 and min(w,h)>5 and x>2 and y>2 and x+w<mask.shape[1]-2 and y+h<mask.shape[0]-2:
            # Edges alone need an illuminated foreground core; do not fill cover seams.
            core = np.zeros_like(mask); cv.drawContours(core,[c],-1,255,-1)
            values = gray[core>0]
            if len(values) and (np.percentile(values,70)>95 or np.median(saturation[core>0])>70):
                cv.drawContours(mask,[c],-1,255,-1)
    mask=cv.morphologyEx(mask,cv.MORPH_OPEN,np.ones((5,5),np.uint8))
    cs,_ = cv.findContours(mask,cv.RETR_EXTERNAL,cv.CHAIN_APPROX_NONE)
    separated=[]
    b,g,r=cv.split(image.astype(np.float32))
    for c in cs:
        if cv.contourArea(c)<mask.size*.04:
            separated.append(c);continue
        # A blue lighter may touch a pale sheet. Split only where independently
        # supported by chroma; never invent a separation between overlapping keys.
        region=np.zeros_like(mask);cv.drawContours(region,[c],-1,255,-1)
        blue=((b>r*1.15)&(b>70)&(region>0)).astype(np.uint8)*255
        pale=((r>b*.98)&(gray>115)&(region>0)).astype(np.uint8)*255
        pale=cv.morphologyEx(pale,cv.MORPH_CLOSE,np.ones((7,7),np.uint8))
        pcs,_=cv.findContours(pale,cv.RETR_EXTERNAL,cv.CHAIN_APPROX_NONE)
        paper=max(pcs,key=cv.contourArea) if pcs else None
        if paper is not None and cv.contourArea(paper)>mask.size*.04 and np.count_nonzero(blue)>1000:
            pm=np.zeros_like(mask);cv.drawContours(pm,[paper],-1,255,-1)
            remainder=cv.bitwise_and(region,cv.bitwise_not(cv.dilate(pm,np.ones((3,3),np.uint8))))
            others,_=cv.findContours(remainder,cv.RETR_EXTERNAL,cv.CHAIN_APPROX_NONE)
            separated.extend([paper,*others])
        else: separated.append(c)
    cs=separated
    candidates=[]
    for c in cs:
        x,y,w,h=cv.boundingRect(c)
        if cv.contourArea(c)<max(180,mask.size*.0005) or min(w,h)<6:
            continue
        if x<3 or y<3 or x+w>=mask.shape[1]-2 or y+h>=mask.shape[0]-2:
            continue
        candidates.append(c)
    return sorted(candidates,key=lambda c: tuple(np.mean(c.reshape(-1,2),axis=0)))


def reference_index(contours, name, excluded=(), main_area=None):
    spec=get_reference(name); ranked=[]
    for i,c in enumerate(contours):
        if i in excluded: continue
        lo,hi=sorted(cv.minAreaRect(c)[1])
        fill=cv.contourArea(c)/max(1,lo*hi)
        score=abs(np.log((hi/max(lo,1))/(spec.height/spec.width)))
        if main_area is not None and not .3 < cv.contourArea(c)/main_area < 2.3:
            continue
        if fill>.65 and score < (.85 if main_area is not None else .35):
            ranked.append((score,i))
    ranked.sort()
    if not ranked: raise ScanError('No isolated object with reference shape: '+name)
    if len(ranked)>1 and ranked[1][0]-ranked[0][0]<.015:
        raise ScanError('Ambiguous reference shape: '+name)
    return ranked[0][1]


def grid_calibration(image, contour, scale, pitch):
    """Fit a lattice homography to detected grid lines, not paper dimensions.

    A provisional quadrilateral warp only groups lines. Independent lattice
    intersections determine the final homography and preserve irregular edges.
    """
    hull=cv.convexHull(contour)
    q=cv.approxPolyDP(hull,.025*cv.arcLength(hull,True),True).reshape(-1,2)
    if len(q)!=4: return None
    q=q[np.argsort(np.arctan2(q[:,1]-q[:,1].mean(),q[:,0]-q[:,0].mean()))]
    q=np.roll(q,-np.argmin(q.sum(axis=1)),axis=0).astype(np.float32)
    provisional=cv.getPerspectiveTransform(q,np.float32([[0,0],[999,0],[999,999],[0,999]]))
    warp=cv.warpPerspective(image,provisional,(1000,1000))
    gray=cv.cvtColor(warp,cv.COLOR_BGR2GRAY)
    dark=cv.subtract(cv.GaussianBlur(gray,(0,0),8),gray)
    binary=(dark>8).astype(np.uint8)*255
    binary[:30]=0;binary[-30:]=0;binary[:,:30]=0;binary[:,-30:]=0
    families=[]
    for vertical in (True,False):
        opened=cv.morphologyEx(binary,cv.MORPH_OPEN,np.ones((45,1) if vertical else (1,45),np.uint8))
        lines=cv.HoughLinesP(opened,1,np.pi/1800,80,minLineLength=180,maxLineGap=30)
        groups=[]
        for x1,y1,x2,y2 in ([] if lines is None else lines[:,0]):
            if vertical: x1,y1,x2,y2=y1,x1,y2,x2
            if abs(x2-x1)<100 or abs((y2-y1)/(x2-x1))>.12: continue
            slope=(y2-y1)/(x2-x1); intercept=y1-slope*x1
            pos=slope*500+intercept
            group=next((g for g in groups if abs(np.mean([v[0] for v in g])-pos)<8),None)
            if group is None: groups.append([(pos,slope,intercept)])
            else: group.append((pos,slope,intercept))
        rows=sorted([np.mean(g,axis=0) for g in groups],key=lambda v:v[0])
        if len(rows)<6: return None
        gaps=np.diff([r[0] for r in rows]); spacing=float(np.median(gaps))
        if spacing<12 or spacing>160: return None
        indices=np.r_[0,np.cumsum(np.rint(gaps/spacing).astype(int))]
        if np.any(np.abs(gaps/spacing-np.rint(gaps/spacing))>.22): return None
        families.append((rows,indices))
    pixels=[];world=[]
    for v,ix in zip(*families[0]):
        for h,iy in zip(*families[1]):
            # vertical: x = v.slope*y + v.intercept; horizontal: y = h.slope*x + h.intercept
            x=(v[2]+v[1]*h[2])/(1-v[1]*h[1]); y=h[1]*x+h[2]
            pixels.append([x,y]);world.append([ix*pitch,iy*pitch])
    pixels=np.float32(pixels);world=np.float32(world)
    H,inliers=cv.findHomography(pixels,world,cv.RANSAC,pitch*.12)
    if H is None or inliers.mean()<.85: return None
    residual=float(np.sqrt(np.mean(np.sum((project(pixels,H)-world)**2,axis=1))))
    if residual>pitch*.15: return None
    original=project(pixels,np.linalg.inv(provisional)).reshape(len(families[0][0]),len(families[1][0]),2)
    measured=[]
    for axis in (0,1):
        steps=np.diff(original,axis=axis)
        indices=families[axis][1]
        divisor=np.diff(indices).reshape((-1,1) if axis==0 else (1,-1))
        measured.append(float(np.median(np.linalg.norm(steps,axis=2)/divisor)*scale))
    return H@provisional,dict(assumed_pitch_mm=pitch,measured_pitch_scale_only_mm=measured,
        lattice_residual_mm=residual,intersection_count=len(pixels),inlier_fraction=float(inliers.mean()),
        notice='Measured pitches use main-reference scale before correction; confirm physical grid pitch. Residual is fit consistency, not accuracy.')


def table_pt(summary):
    lines=['| Objeto | Correção | Medidas (mm) |', '|---|---|---|']
    for o in summary['objects']:
        if o.get('ignored'):
            value=o['reason']
        elif 'arms' in o:
            value=f"longo {o['arms']['long_mm']:.1f}; curto {o['arms']['short_mm']:.1f}"
        elif o['kind']=='papel quadriculado':
            value='; '.join(f"lado {i+1}: {v:.1f}" for i,v in enumerate(o['sides_mm']))
        else:
            value=f"{o['length_mm']:.1f} × {o['width_mm']:.1f}"
        lines.append(f"| {o['id']} ({o['kind']}) | {o['correction']} | {value} |")
    return '\n'.join(lines)


def _measure_scale_only(image, reference, check_reference=None, grid_pitch_mm=5.):
    if not reference: raise ScanError('Multi-object mode requires --reference')
    if not np.isfinite(grid_pitch_mm) or grid_pitch_mm<=0: raise ValueError('Grid pitch must be positive and finite')
    contours=segments(image)
    main=reference_index(contours,reference)
    spec=get_reference(reference)
    scale=spec.height/max(cv.minAreaRect(contours[main])[1])
    check=reference_index(contours,check_reference,(main,),cv.contourArea(contours[main])*get_reference(check_reference).width*get_reference(check_reference).height/(spec.width*spec.height)) if check_reference and check_reference!='bic-mini' else None
    overlay=image.copy();objects=[];pieces=[];checks=None
    warnings=['Experimental segmentation: inspect numbered overlay for missed/merged objects and reflections.',
        'Scale-only references: perspective, raised lighter bodies and lens distortion cause spatially varying errors. No certified accuracy.',
        'Polygon sides describe silhouette edges; L-key outer/inner arms differ and bends are approximated.',
        'Objects calibrated on different planes share approximate placement in DXF; do not infer distances between objects.',
        spec.warning.replace("Frontal photo required.", "Perspective remains uncorrected outside detected grids.")]
    if check is not None:
        dims=sorted(cv.minAreaRect(contours[check])[1]); expected=get_reference(check_reference)
        measured=np.array(dims)*scale; nominal=np.array([expected.width,expected.height])
        delta=100*(measured/nominal-1)
        checks=dict(reference=check_reference,measured_width_mm=float(measured[0]),measured_length_mm=float(measured[1]),
            nominal_width_mm=expected.width,nominal_length_mm=expected.height,
            difference_percent=delta.tolist(),error_estimate_percent=float(max(abs(delta))),
            notice='Cross-reference disagreement, not an upper error bound; both dimensions are approximate.')
    if checks is not None and checks['error_estimate_percent']>10:
        warnings.append(f"Check-reference disagreement {checks['error_estimate_percent']:.1f}%: dimensions are not reliable for fabrication.")
    for i,c in enumerate(contours):
        isref=i in (main,check)
        cv.drawContours(overlay,[c],-1,(0,200,0) if isref else (0,180,255),2)
        if isref:
            x,y,w,h=cv.boundingRect(c);cv.putText(overlay,reference if i==main else check_reference,(x,max(20,y-10)),cv.FONT_HERSHEY_SIMPLEX,.65,(0,255,0),2)
            continue
        ident=len(objects)+1
        fill=cv.contourArea(c)/max(1,np.prod(cv.minAreaRect(c)[1]))
        kind='papel/corpo compacto' if fill>.65 else 'chave L/objeto fino'
        region=np.zeros(image.shape[:2],np.uint8);cv.drawContours(region,[c],-1,255,-1)
        gray=cv.cvtColor(image,cv.COLOR_BGR2GRAY)
        uncertain=bool(np.percentile(gray[region>0],80)<110)
        if uncertain: kind='candidato incerto/possível fundo'
        correction='escala apenas';grid=None
        calibrated=grid_calibration(image,c,scale,grid_pitch_mm) if fill>.7 else None
        H=np.diag([scale,scale,1.])
        if calibrated is not None:
            H,grid=calibrated
            center=c.reshape(-1,2).mean(axis=0)
            anchor=project([center],H)[0]
            inverse=np.linalg.inv(H)
            basis=project([anchor,anchor+[1,0],anchor+[0,1]],inverse)
            u,_,vt=np.linalg.svd((basis[1:]-basis[0]).T)
            rotation=u@vt
            align=np.eye(3);align[:2,:2]=rotation;align[:2,2]=center*scale-rotation@anchor
            H=align@H
            correction='homografia local da malha';kind='papel quadriculado'
        metric=project(c.reshape(-1,2),H).astype(np.float32)
        # Small tolerance preserves concavities and suppresses pixel noise.
        epsilon=max(.4,.007*cv.arcLength(metric.reshape(-1,1,2),True))
        poly=cv.approxPolyDP(metric,epsilon,True).reshape(-1,2)
        sides=np.linalg.norm(poly-np.roll(poly,-1,axis=0),axis=1)
        width,length=sorted(cv.minAreaRect(metric)[1])
        pieces.append(Piece(poly,[]))
        obj=dict(id=ident,kind=kind,width_mm=float(width),length_mm=float(length),sides_mm=sides.tolist(),
            polygon_mm=poly.tolist(),correction=correction,grid=grid,
            uncertain_segmentation=uncertain)
        objects.append(obj)
        pxpoly=project(poly,np.linalg.inv(H));cv.polylines(overlay,[np.rint(pxpoly).astype(np.int32)],True,(0,0,255),2)
        x,y,w,h=cv.boundingRect(c)
        cv.putText(overlay,f'{ident}: {length:.1f} x {width:.1f} mm',(max(5,x-20),max(25,y-15)),cv.FONT_HERSHEY_SIMPLEX,.65,(0,255,255),2)
        for j,(a,b,s) in enumerate(zip(pxpoly,np.roll(pxpoly,-1,axis=0),sides)):
            p=np.rint((a+b)/2).astype(int)
            cv.putText(overlay,f'{j+1}:{s:.1f}',tuple(p),cv.FONT_HERSHEY_SIMPLEX,.43,(30,30,255),1)
    if not pieces: raise ScanError('No non-reference objects found')
    if any(o['uncertain_segmentation'] for o in objects):
        warnings.append('Low-contrast candidate may be background texture; confirm against the overlay before using its dimensions.')
    result=Measurement(pieces[0].contour_mm,sum(p.polygon.area for p in pieces),max(o['width_mm'] for o in objects),max(o['length_mm'] for o in objects),None,None,overlay,[],pieces,{},reference,spec.accuracy,[w for w in warnings if w])
    result.objects=objects;result.check_reference=checks;result.scale_mm_per_pixel=scale
    return result


def arm_measurements(metric):
    """Outer arm extents, with axes fitted to straight silhouette edges.

    Each dimension follows the centreline direction from the tip to the
    outer bend support line (includes the half diameter at the bend).
    """
    poly=cv.approxPolyDP(metric.astype(np.float32), .6, True).reshape(-1,2)
    edges=np.roll(poly,-1,axis=0)-poly
    u=edges[np.argmax(np.linalg.norm(edges,axis=1))].astype(float)
    u/=np.linalg.norm(u);v=np.array([-u[1],u[0]])
    basis=np.array([u,v]); xy=metric@basis.T
    low=xy.min(axis=0); high=xy.max(axis=0)
    # The long shank occupies only one side of the bounding rectangle.
    mid=(low[0]+high[0])/2
    shank=xy[np.abs(xy[:,0]-mid)<(high[0]-low[0])*.2]
    cy=float(np.median(shank[:,1]))
    end=low[0] if np.ptp(xy[xy[:,0]<mid,1])>np.ptp(xy[xy[:,0]>=mid,1]) else high[0]
    short=xy[np.abs(xy[:,1]-cy)>(high[1]-low[1])*.55]
    cx=float(np.median(short[:,0])) if len(short) else end
    outer_y=low[1] if abs(cy-low[1])<abs(cy-high[1]) else high[1]
    tip_x=high[0] if end==low[0] else low[0]
    tip_y=high[1] if outer_y==low[1] else low[1]
    points=[np.array([[tip_x,cy],[end,cy]])@basis,
            np.array([[cx,tip_y],[cx,outer_y]])@basis]
    return dict(long_mm=float(high[0]-low[0]),short_mm=float(high[1]-low[1]),
                definition='centreline direction, outer tip to outer bend support',
                segments_mm=[a.tolist() for a in points])


def measure_all(image, reference, check_reference=None, grid_pitch_mm=5.):
    if not reference: raise ScanError('Multi-object mode requires --reference')
    if not np.isfinite(grid_pitch_mm) or grid_pitch_mm<=0: raise ValueError('Grid pitch must be positive and finite')
    contours=segments(image)
    fits=[]
    for i,c in enumerate(contours):
        dims=cv.minAreaRect(c)[1]
        if cv.contourArea(c)/max(1,np.prod(dims))>.7:
            fit=grid_calibration(image,c,1.,grid_pitch_mm)
            if fit is not None: fits.append((i,fit))
    if not fits:
        return _measure_scale_only(image,reference,check_reference,grid_pitch_mm)
    paper,(H,grid)=max(fits,key=lambda item:item[1][1]['intersection_count'])
    grid['notice']='Plane homography from grid pitch; residual is internal fit consistency, not physical accuracy.'
    grid.pop('measured_pitch_scale_only_mm',None)
    metric=[project(c.reshape(-1,2),H).astype(np.float32) for c in contours]
    spec=get_reference(reference)
    compact=[]
    for i,c in enumerate(contours):
        dims=sorted(cv.minAreaRect(metric[i])[1])
        fill=cv.contourArea(c)/max(1,np.prod(cv.minAreaRect(c)[1]))
        if i!=paper and fill>.65:
            compact.append((abs(np.log(dims[1]/dims[0]/(spec.height/spec.width))) + 2*abs(np.log(dims[1]/spec.height)) + 2*abs(np.log(dims[0]/spec.width)),i))
    if not compact: raise ScanError('No flat check reference on calibrated plane')
    _,main=min(compact)
    objects=[];pieces=[];labels=[]
    overlay=image.copy()
    gray=cv.cvtColor(image,cv.COLOR_BGR2GRAY)
    for i,c in enumerate(contours):
        region=np.zeros(gray.shape,np.uint8);cv.drawContours(region,[c],-1,255,-1)
        fill=cv.contourArea(c)/max(1,np.prod(cv.minAreaRect(c)[1]))
        dims=sorted(cv.minAreaRect(metric[i])[1])
        dark=np.percentile(gray[region>0],80)<110
        # Compact coloured candidates inconsistent with the calibrated J26
        # envelope are raised/foreshortened, never alternate lighter models.
        if i not in (main,paper) and (fill>.65 or dark):
            colour=np.percentile(cv.cvtColor(image,cv.COLOR_BGR2HSV)[:,:,1][region>0],80)>65
            reason='fora do plano' if colour and cv.contourArea(c)>10000 else 'ignorado: textura/costura da capa'
            obj=dict(id=len(objects)+1,kind='BIC J26 em pé' if reason=='fora do plano' else 'fundo',ignored=True,reason=reason,correction='nenhuma: excluído')
            objects.append(obj);cv.drawContours(overlay,[c],-1,(130,130,130),3)
            labels.append((c.reshape(-1,2).mean(axis=0),f"{obj['id']}: {reason}"));continue
        ident=len(objects)+1
        poly=cv.approxPolyDP(metric[i],max(.4,.007*cv.arcLength(metric[i],True)),True).reshape(-1,2)
        sides=np.linalg.norm(poly-np.roll(poly,-1,axis=0),axis=1)
        kind='papel quadriculado' if i==paper else ('BIC J26 deitado' if i==main else 'chave L')
        obj=dict(id=ident,kind=kind,ignored=False,width_mm=float(dims[0]),length_mm=float(dims[1]),sides_mm=sides.tolist(),polygon_mm=poly.tolist(),correction='homografia do plano da mesa (malha 5 mm)',grid=grid if i==paper else None,uncertain_segmentation=False)
        if kind=='chave L': obj['arms']=arm_measurements(metric[i])
        objects.append(obj)
        if i!=main: pieces.append(Piece(poly,[]))
        cv.drawContours(overlay,[c],-1,(0,220,255),3)
        labels.append((c.reshape(-1,2).mean(axis=0),f'{ident}: {kind}'))
        if 'arms' in obj:
            for name,value,seg in zip(('longo','curto'),(obj['arms']['long_mm'],obj['arms']['short_mm']),obj['arms']['segments_mm']):
                pts=project(seg,np.linalg.inv(H));cv.polylines(overlay,[pts.astype(np.int32)],False,(30,255,50),3)
                labels.append((pts.mean(axis=0),f'{ident} {name}: {value:.1f} mm'))
        elif i==main:
            labels.append((c.reshape(-1,2).mean(axis=0)+[0,55],f'{ident} confere: {dims[1]:.1f} mm'))
        elif i==paper:
            px=project(poly,np.linalg.inv(H))
            for j,(a,b,value) in enumerate(zip(px,np.roll(px,-1,axis=0),sides)):
                labels.append(((a+b)/2,f'{ident} lado {j+1}: {value:.1f} mm'))
    dims=sorted(cv.minAreaRect(metric[main])[1]);delta=100*(dims[1]/spec.height-1)
    checks=dict(reference=reference,measured_length_mm=float(dims[1]),measured_width_mm=float(dims[0]),nominal_length_mm=spec.height,nominal_width_mm=spec.width,difference_percent=[100*(dims[0]/spec.width-1),delta],error_estimate_percent=abs(delta),notice='Flat J26 length discrepancy after plane rectification; not a guaranteed bound.')
    # PIL provides accented text and a true >=28 pixel font.
    from PIL import Image,ImageDraw,ImageFont
    canvas=Image.fromarray(cv.cvtColor(overlay,cv.COLOR_BGR2RGB))
    draw=ImageDraw.Draw(canvas);font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',28)
    occupied=[]
    def label(point,text):
        box=draw.textbbox((0,0),text,font=font);w=box[2]+16;h=40
        x=int(np.clip(point[0],4,canvas.width-w-4));y=int(np.clip(point[1],4,canvas.height-h-4))
        for _ in range(35):
            if not any(x<a+aw and x+w>a and y<b+bh and y+h>b for a,b,aw,bh in occupied):break
            y=(y+44)%(canvas.height-h)
        occupied.append((x,y,w,h));draw.rounded_rectangle((x,y,x+w,y+h),8,fill=(20,25,32));draw.text((x+8,y+2),text,font=font,fill='white')
    # Add an expanded top margin for a readable measurement legend.
    legend=['PLANO: malha 5 mm | medidas em mm']
    for o in objects:
        if o.get('ignored'):legend.append(f"{o['id']} {o['kind']}: {o['reason']}")
        elif 'arms' in o:legend.append(f"{o['id']} chave: longo {o['arms']['long_mm']:.1f} | curto {o['arms']['short_mm']:.1f}")
        elif o['kind']=='papel quadriculado':legend.append(f"{o['id']} papel: "+' | '.join(f'{v:.1f}' for v in o['sides_mm']))
        else:legend.append(f"{o['id']} J26 deitado: {dims[1]:.1f} / {spec.height:.1f} nominal | erro {delta:+.1f}%")
    for point,text in labels:label(point,text)
    top=44*len(legend)+16
    final=Image.new('RGB',(canvas.width,canvas.height+top),(20,25,32));final.paste(canvas,(0,top));d=ImageDraw.Draw(final)
    for j,line in enumerate(legend):d.text((16,8+j*44),line,font=font,fill='white')
    result=Measurement(pieces[0].contour_mm,sum(p.polygon.area for p in pieces),max(o.get('width_mm',0) for o in objects),max(o.get('length_mm',0) for o in objects),None,None,cv.cvtColor(np.array(final),cv.COLOR_RGB2BGR),[],pieces,{},reference,spec.accuracy,['Plane extrapolation assumes paper and objects share a plane; flat lighter thickness and paper curvature remain sources of error.'])
    result.objects=objects;result.check_reference=checks;result.scale_mm_per_pixel=None
    return result
