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
