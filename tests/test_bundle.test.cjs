const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const source=path.resolve(__dirname,'../server/web/test-bundle.js');
const bundle=require(fs.existsSync(source)?source:'../waterwatch_esphome/server/web/test-bundle.js');
const photo=fs.readFileSync(path.join(__dirname,'fixtures/clean.png'));
const example={raw_value:'00???.88?',confidence:0,reason:'ambiguous_digit',would_accept:false,
  details:{digits:[{digit:2,recognitions:[[null,0],[null,0]]}]},
  calibration:{id:'house',rois:[{x:.1,y:.2,w:.05,h:.2,rotation_degrees:-17}]},
  images:{original:{mime:'image/png',base64:photo.toString('base64')},digits:[]}};
test('export preserves exact photograph bytes, calibration and individual failures',()=>{
  const result=JSON.parse(bundle.serialize(example,'123.456'));
  const restored=Buffer.from(result.images.original.base64,'base64');
  assert.equal(crypto.createHash('sha256').update(restored).digest('hex'),crypto.createHash('sha256').update(photo).digest('hex'));
  assert.deepEqual(result.calibration,example.calibration);assert.deepEqual(result.details,example.details);
  assert.equal(result.human_label,'123.456');assert.equal(result.would_accept,false);
  assert.equal(result.kind,'waterwatch_ocr_test');
});
test('a human label does not replace OCR or turn the example into a recorded reading',()=>{
  const result=JSON.parse(bundle.serialize({...example,authorization:'do-not-export',event_id:'not-a-reading'},'0.000'));
  assert.equal(result.raw_value,'00???.88?');assert.equal(result.confidence,0);
  assert.equal(result.human_label,'0.000');assert.equal(result.authorization,undefined);assert.equal(result.event_id,undefined);
});
test('empty verified label stays explicitly unset',()=>assert.equal(JSON.parse(bundle.serialize(example)).human_label,null));
for(const label of ['-1','12,3','1.1234567','1000000000','not verified'])test(`invalid label ${label} is rejected`,()=>assert.throws(()=>bundle.serialize(example,label)));
test('missing images or recorded results cannot be exported as an unrecorded test',()=>{
  assert.throws(()=>bundle.serialize(null));assert.throws(()=>bundle.serialize({...example,images:{}}));
  assert.throws(()=>bundle.serialize({...example,would_accept:undefined,accepted:true}));
});
