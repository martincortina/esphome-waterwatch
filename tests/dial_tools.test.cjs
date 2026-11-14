const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const local=path.resolve(__dirname,'../server/web/dial-tools.js'),dial=require(fs.existsSync(local)?local:'../waterwatch_esphome/server/web/dial-tools.js');
test('dial points use the rotated region coordinate system',()=>{
  const corners=[{x:.6,y:.1},{x:.6,y:.5},{x:.2,y:.5},{x:.2,y:.1}];
  const p=dial.local({}, {x:.4,y:.3},corners);assert.ok(Math.abs(p.x-.5)<1e-9&&Math.abs(p.y-.5)<1e-9);
});
test('zero reference is up, then clockwise, without a full-turn value',()=>{
  for(const [p,angle] of [[{x:.5,y:.1},0],[{x:.9,y:.5},90],[{x:.5,y:.9},180],[{x:.1,y:.5},270]])assert.equal(dial.zero(dial.defaults(),p),angle);
});
test('including a preview dial verifies its scale and adds exactly one decimal',()=>{
  const cfg={decimal_places:3,rois:[{}, {kind:'analog',include_in_reading:false}]};
  assert.deepEqual(dial.includePlan(cfg,1,'0.0001'),{decimal_places:4,ignored_decimal_digits:0});
  for(const value of ['', '0.001','0.00001','unknown','NaN'])assert.throws(()=>dial.includePlan(cfg,1,value));
  assert.throws(()=>dial.includePlan({...cfg,rois:[cfg.rois[1],{}]},0,'0.0001'));
});
test('dial circle cannot leave its region',()=>{
  assert.equal(dial.valid(dial.defaults()),true);assert.equal(dial.valid({...dial.defaults(),center_x:.2}),false);assert.equal(dial.valid({...dial.defaults(),zero_angle_degrees:360}),false);
});
