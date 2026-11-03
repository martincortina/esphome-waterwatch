/* Pure geometry shared by the digit editor and its Node tests. */
"use strict";
var RoiGeometry = (() => {
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  function localPoint(roi, p, size={width:1,height:1}, inverse=false) {
    const cx=roi.x+roi.w/2,cy=roi.y+roi.h/2,theta=(roi.rotation_degrees||0)*Math.PI/180*(inverse?-1:1);
    const dx=(p.x-cx)*size.width,dy=(p.y-cy)*size.height,c=Math.cos(theta),s=Math.sin(theta);
    return {x:cx+(dx*c-dy*s)/size.width,y:cy+(dx*s+dy*c)/size.height};
  }
  function handles(roi, size) {
    const left=roi.x, top=roi.y, right=roi.x+roi.w, bottom=roi.y+roi.h;
    const points={nw:{x:left,y:top},n:{x:(left+right)/2,y:top},ne:{x:right,y:top},e:{x:right,y:(top+bottom)/2},se:{x:right,y:bottom},s:{x:(left+right)/2,y:bottom},sw:{x:left,y:bottom},w:{x:left,y:(top+bottom)/2}};
    return Object.fromEntries(Object.entries(points).map(([key,p])=>[key,localPoint(roi,p,size)]));
  }
  function corners(roi,size) { const h=handles(roi,size);return [h.nw,h.ne,h.se,h.sw]; }
  function contains(roi, point, size) { const p=localPoint(roi,point,size,true);return p.x>=roi.x && p.x<=roi.x+roi.w && p.y>=roi.y && p.y<=roi.y+roi.h; }
  function hitTest(rois, point, tolerance, selected=-1, size, rotationPoint=null) {
    if (rois[selected]) {
      if(rotationPoint && Math.abs(point.x-rotationPoint.x)<=tolerance.x && Math.abs(point.y-rotationPoint.y)<=tolerance.y)return {index:selected,handle:"rotate"};
      for (const [handle,position] of Object.entries(handles(rois[selected],size))) {
        if (Math.abs(point.x-position.x)<=tolerance.x && Math.abs(point.y-position.y)<=tolerance.y) return {index:selected,handle};
      }
      if (contains(rois[selected], point,size)) return {index:selected,handle:"move"};
    }
    for (let index=rois.length-1;index>=0;index--) if (contains(rois[index],point,size)) return {index,handle:"move"};
    return null;
  }
  function fit(roi, size={width:1,height:1}) {
    const angle=(roi.rotation_degrees||0)*Math.PI/180,c=Math.abs(Math.cos(angle)),s=Math.abs(Math.sin(angle));
    const marginX=Math.max(0,(roi.w*c+roi.h*size.height/size.width*s-roi.w)/2);
    const marginY=Math.max(0,(roi.h*c+roi.w*size.width/size.height*s-roi.h)/2);
    if(roi.w+2*marginX>1+1e-12 || roi.h+2*marginY>1+1e-12)return null;
    return {...roi,x:clamp(roi.x,marginX,1-roi.w-marginX),y:clamp(roi.y,marginY,1-roi.h-marginY)};
  }
  function inside(roi,size) { return roi.x>=0 && roi.y>=0 && roi.w<=1 && roi.h<=1 && roi.x+roi.w<=1 && roi.y+roi.h<=1 && corners(roi,size).every(p=>p.x>=-1e-12 && p.x<=1+1e-12 && p.y>=-1e-12 && p.y<=1+1e-12); }
  function move(roi, dx, dy, size) { return fit({...roi,x:roi.x+dx,y:roi.y+dy},size) || {...roi}; }
  function resize(roi, handle, point, minimum, size) {
    if(roi.rotation_degrees) {
      const p=localPoint(roi,point,size,true),origin=handles({...roi,rotation_degrees:0})[handle];
      function candidate(t) {
        const q={x:origin.x+(p.x-origin.x)*t,y:origin.y+(p.y-origin.y)*t};
        let left=roi.x,top=roi.y,right=roi.x+roi.w,bottom=roi.y+roi.h;
        if(handle.includes("w"))left=Math.min(q.x,right-Math.min(roi.w,minimum.w));
        if(handle.includes("e"))right=Math.max(q.x,left+Math.min(roi.w,minimum.w));
        if(handle.includes("n"))top=Math.min(q.y,bottom-Math.min(roi.h,minimum.h));
        if(handle.includes("s"))bottom=Math.max(q.y,top+Math.min(roi.h,minimum.h));
        const center=localPoint(roi,{x:(left+right)/2,y:(top+bottom)/2},size);
        return {...roi,x:center.x-(right-left)/2,y:center.y-(bottom-top)/2,w:right-left,h:bottom-top};
      }
      const full=candidate(1);if(inside(full,size))return full;
      let low=0,high=1;for(let i=0;i<40;i++){const mid=(low+high)/2;if(inside(candidate(mid),size))low=mid;else high=mid;}
      return candidate(low);
    }
    let left=roi.x, top=roi.y, right=roi.x+roi.w, bottom=roi.y+roi.h;
    const minW=Math.min(roi.w,minimum.w), minH=Math.min(roi.h,minimum.h);
    if (handle.includes("w")) left=clamp(point.x,0,right-minW);
    if (handle.includes("e")) right=clamp(point.x,left+minW,1);
    if (handle.includes("n")) top=clamp(point.y,0,bottom-minH);
    if (handle.includes("s")) bottom=clamp(point.y,top+minH,1);
    return {...roi,x:left,y:top,w:right-left,h:bottom-top};
  }
  function fromPoints(a, b, minimum) {
    const left=clamp(Math.min(a.x,b.x),0,1),right=clamp(Math.max(a.x,b.x),0,1);
    const top=clamp(Math.min(a.y,b.y),0,1),bottom=clamp(Math.max(a.y,b.y),0,1);
    if (right-left<minimum.w || bottom-top<minimum.h) return null;
    return {x:left,y:top,w:right-left,h:bottom-top};
  }
  function rotate(roi, degrees, size) { return fit({...roi,rotation_degrees:Math.round((((degrees+180)%360+360)%360-180)*10)/10},size); }
  function rotationHandle(roi,offset,size) { return localPoint(roi,{x:roi.x+roi.w/2,y:roi.y-offset/size.height},size); }
  function angleAt(roi,p,size) { return Math.atan2((p.y-roi.y-roi.h/2)*size.height,(p.x-roi.x-roi.w/2)*size.width)*180/Math.PI; }
  return {handles,corners,localPoint,contains,hitTest,move,resize,fromPoints,rotate,rotationHandle,angleAt,inside};
})();
if (typeof module !== "undefined" && module.exports) module.exports = RoiGeometry;
