/* Pure geometry shared by the digit editor and its Node tests. */
"use strict";
var RoiGeometry = (() => {
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  function handles(roi) {
    const left=roi.x, top=roi.y, right=roi.x+roi.w, bottom=roi.y+roi.h;
    return {nw:{x:left,y:top},n:{x:(left+right)/2,y:top},ne:{x:right,y:top},e:{x:right,y:(top+bottom)/2},se:{x:right,y:bottom},s:{x:(left+right)/2,y:bottom},sw:{x:left,y:bottom},w:{x:left,y:(top+bottom)/2}};
  }
  function contains(roi, point) { return point.x>=roi.x && point.x<=roi.x+roi.w && point.y>=roi.y && point.y<=roi.y+roi.h; }
  function hitTest(rois, point, tolerance, selected=-1) {
    if (rois[selected]) {
      for (const [handle,position] of Object.entries(handles(rois[selected]))) {
        if (Math.abs(point.x-position.x)<=tolerance.x && Math.abs(point.y-position.y)<=tolerance.y) return {index:selected,handle};
      }
      if (contains(rois[selected], point)) return {index:selected,handle:"move"};
    }
    for (let index=rois.length-1;index>=0;index--) if (contains(rois[index],point)) return {index,handle:"move"};
    return null;
  }
  function move(roi, dx, dy) { return {...roi,x:clamp(roi.x+dx,0,1-roi.w),y:clamp(roi.y+dy,0,1-roi.h)}; }
  function resize(roi, handle, point, minimum) {
    let left=roi.x, top=roi.y, right=roi.x+roi.w, bottom=roi.y+roi.h;
    const minW=Math.min(roi.w,minimum.w), minH=Math.min(roi.h,minimum.h);
    if (handle.includes("w")) left=clamp(point.x,0,right-minW);
    if (handle.includes("e")) right=clamp(point.x,left+minW,1);
    if (handle.includes("n")) top=clamp(point.y,0,bottom-minH);
    if (handle.includes("s")) bottom=clamp(point.y,top+minH,1);
    return {x:left,y:top,w:right-left,h:bottom-top};
  }
  function fromPoints(a, b, minimum) {
    const left=clamp(Math.min(a.x,b.x),0,1),right=clamp(Math.max(a.x,b.x),0,1);
    const top=clamp(Math.min(a.y,b.y),0,1),bottom=clamp(Math.max(a.y,b.y),0,1);
    if (right-left<minimum.w || bottom-top<minimum.h) return null;
    return {x:left,y:top,w:right-left,h:bottom-top};
  }
  return {handles,contains,hitTest,move,resize,fromPoints};
})();
if (typeof module !== "undefined" && module.exports) module.exports = RoiGeometry;
