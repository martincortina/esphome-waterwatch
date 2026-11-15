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
function counter(){return {decimal_places:3,ignored_decimal_digits:0,rois:[...Array.from({length:8},()=>({kind:'digit'})),...Array.from({length:3},()=>({kind:'analog',include_in_reading:false}))]};}
test('each additional dial is one tenth of the preceding dial, including preview scale',()=>{
  const cfg=counter();
  assert.equal(dial.scalePlan(cfg,8).step,.0001);assert.equal(dial.scalePlan(cfg,9).step,.00001);assert.equal(dial.scalePlan(cfg,10).step,.000001);
  for(let index=8;index<11;index++){
    const plan=dial.scalePlan(cfg,index);assert.equal(plan.ordinal,index-7);assert.equal(plan.parent,index-1);
    Object.assign(cfg,dial.includePlan(cfg,index,'previous/10'));cfg.rois[index].include_in_reading=true;
  }
  assert.equal(cfg.decimal_places,6);assert.equal(dial.scalePlan(cfg,8).step,.0001);assert.equal(dial.scalePlan(cfg,10).step,.000001);
});
test('a finer dial cannot skip a preview-only predecessor or change a coarser scale',()=>{
  const cfg=counter();assert.throws(()=>dial.includePlan(cfg,9,'previous/10'),/preceding/);
  Object.assign(cfg,dial.includePlan(cfg,8,'previous/10'));cfg.rois[8].include_in_reading=true;
  Object.assign(cfg,dial.includePlan(cfg,9,'previous/10'));cfg.rois[9].include_in_reading=true;
  assert.throws(()=>dial.removePlan(cfg,8),/finest/);
  const removal=dial.removePlan(cfg,9);assert.equal(removal.decimal_places,4);
});
test('millilitre precision limit is explained before including an extra dial',()=>{
  const cfg=counter();for(let index=8;index<11;index++){Object.assign(cfg,dial.includePlan(cfg,index,'previous/10'));cfg.rois[index].include_in_reading=true;}
  cfg.rois.push({kind:'analog',include_in_reading:false});assert.equal(dial.scalePlan(cfg,11).can_include,false);
  assert.throws(()=>dial.includePlan(cfg,11,'previous/10'),/six decimal/);
});
