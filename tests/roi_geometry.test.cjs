const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = path.resolve(__dirname, '../server/web/roi-geometry.js');
const geometry = require(fs.existsSync(source) ? source : '../waterwatch_esphome/server/web/roi-geometry.js');
const roi = {x:.2,y:.3,w:.25,h:.4};
const minimum = {w:.02,h:.04};
const close = (actual, expected) => assert.ok(Math.abs(actual-expected)<1e-10, `${actual} != ${expected}`);

test('moving preserves size and stays inside image at every boundary', () => {
  const moved = geometry.move(roi,-5,8);
  assert.deepEqual(moved,{x:0,y:.6,w:.25,h:.4});
  assert.deepEqual(roi,{x:.2,y:.3,w:.25,h:.4});
  assert.deepEqual(geometry.move(roi,5,-8),{x:.75,y:0,w:.25,h:.4});
});
test('all eight resize handles leave the opposite side anchored', () => {
  for (const handle of Object.keys(geometry.handles(roi))) {
    const box=geometry.resize(roi,handle,{x:.4,y:.5},minimum);
    if(!handle.includes('w')) close(box.x,roi.x);
    if(!handle.includes('n')) close(box.y,roi.y);
    if(!handle.includes('e')) close(box.x+box.w,roi.x+roi.w);
    if(!handle.includes('s')) close(box.y+box.h,roi.y+roi.h);
    assert.ok(box.w>=minimum.w-1e-10 && box.h>=minimum.h-1e-10);
  }
});
test('resize cannot cross the opposite corner or leave the image', () => {
  const collapsed=geometry.resize(roi,'nw',{x:2,y:2},minimum);
  close(collapsed.w,minimum.w);close(collapsed.h,minimum.h);
  close(collapsed.x+collapsed.w,roi.x+roi.w);
  const expanded=geometry.resize(roi,'se',{x:2,y:2},minimum);
  close(expanded.x+expanded.w,1);close(expanded.y+expanded.h,1);
});
test('selected corner handles take precedence over the rectangle body', () => {
  assert.deepEqual(geometry.hitTest([roi],{x:.205,y:.305},{x:.01,y:.01},0),{index:0,handle:'nw'});
  assert.deepEqual(geometry.hitTest([roi],{x:.3,y:.5},{x:.01,y:.01},0),{index:0,handle:'move'});
});
test('unselected overlap selects the last drawn box without reordering digits', () => {
  const list=[roi,{...roi}];
  assert.deepEqual(geometry.hitTest(list,{x:.3,y:.5},{x:.01,y:.01}),{index:1,handle:'move'});
  assert.deepEqual(list,[roi,{...roi}]);
});
test('selected box remains draggable in overlapping regions', () => {
  assert.deepEqual(geometry.hitTest([roi,{...roi}],{x:.3,y:.5},{x:.01,y:.01},0),{index:0,handle:'move'});
});
test('hit testing works with different horizontal and vertical zoom scales', () => {
  assert.deepEqual(geometry.hitTest([roi],{x:.453,y:.704},{x:.005,y:.01},0),{index:0,handle:'se'});
  assert.equal(geometry.hitTest([roi],{x:.6,y:.5},{x:.005,y:.01},0),null);
});
test('drawing backwards normalizes coordinates and enforces minimum size', () => {
  assert.deepEqual(geometry.fromPoints({x:.5,y:.7},{x:.2,y:.3},minimum),{x:.2,y:.3,w:.3,h:.39999999999999997});
  assert.equal(geometry.fromPoints({x:.2,y:.3},{x:.21,y:.31},minimum),null);
  assert.deepEqual(geometry.fromPoints({x:-1,y:-1},{x:2,y:2},minimum),{x:0,y:0,w:1,h:1});
});

const imageSize={width:320,height:240};
const oriented={x:140/320,y:80/240,w:40/320,h:80/240,rotation_degrees:90};
test('quarter-turn digit geometry uses pixels on a nonsquare image',()=>{
  const points=geometry.corners(oriented,imageSize);
  const expected=[[200,100],[200,140],[120,140],[120,100]];
  points.forEach((p,i)=>{close(p.x*320,expected[i][0]);close(p.y*240,expected[i][1]);});
  assert.equal(geometry.contains(oriented,{x:190/320,y:120/240},imageSize),true);
  assert.equal(geometry.contains(oriented,{x:160/320,y:90/240},imageSize),false);
});
test('rotated box movement clamps every corner and preserves angle and size',()=>{
  const moved=geometry.move(oriented,-8,9,imageSize);
  assert.equal(geometry.inside(moved,imageSize),true);
  close(moved.w,oriented.w);close(moved.h,oriented.h);assert.equal(moved.rotation_degrees,90);
});
test('all rotated resize handles keep the opposite world anchor fixed',()=>{
  const box={...oriented,rotation_degrees:27},opposite={nw:'se',n:'s',ne:'sw',e:'w',se:'nw',s:'n',sw:'ne',w:'e'};
  for(const [handle,other] of Object.entries(opposite)){
    const old=geometry.handles(box,imageSize),p={x:old[handle].x+.01,y:old[handle].y+.02};
    const resized=geometry.resize(box,handle,p,{w:9/320,h:9/240},imageSize);
    const anchor=geometry.handles(resized,imageSize)[other];
    close(anchor.x,old[other].x);close(anchor.y,old[other].y);
    assert.equal(resized.rotation_degrees,27);assert.equal(geometry.inside(resized,imageSize),true);
  }
});
test('rotated resize stops at the image boundary without moving the opposite corner',()=>{
  const box={...oriented,rotation_degrees:30},before=geometry.handles(box,imageSize).nw;
  const resized=geometry.resize(box,'se',{x:2,y:2},{w:9/320,h:9/240},imageSize);
  assert.equal(geometry.inside(resized,imageSize),true);
  const after=geometry.handles(resized,imageSize).nw;close(before.x,after.x);close(before.y,after.y);
});
test('angle changes shift near-edge boxes into bounds or reject impossible fits',()=>{
  const changed=geometry.rotate({...oriented,x:0,y:0,rotation_degrees:0},90,imageSize);
  assert.equal(geometry.inside(changed,imageSize),true);
  assert.equal(geometry.rotate({x:0,y:0,w:1,h:1},90,imageSize),null);
  assert.equal(geometry.rotate(oriented,270,imageSize).rotation_degrees,-90);
});
test('rotated handles and external rotation knob are hit before a box body',()=>{
  const points=geometry.handles(oriented,imageSize),knob=geometry.rotationHandle(oriented,25,imageSize);
  assert.deepEqual(geometry.hitTest([oriented],points.nw,{x:.01,y:.01},0,imageSize,knob),{index:0,handle:'nw'});
  assert.deepEqual(geometry.hitTest([oriented],knob,{x:.01,y:.01},0,imageSize,knob),{index:0,handle:'rotate'});
  close(geometry.angleAt(oriented,knob,imageSize),0);
});
