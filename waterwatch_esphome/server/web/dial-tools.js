(function(root,factory){const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;else root.DialTools=api;})(typeof globalThis!=="undefined"?globalThis:this,function(){
  "use strict";
  function defaults(){return {center_x:.5,center_y:.5,radius:.45,zero_angle_degrees:0,clockwise:true,pointer_color:"red"};}
  function local(roi,point,corners){
    const [a,b,,d]=corners,x={x:b.x-a.x,y:b.y-a.y},y={x:d.x-a.x,y:d.y-a.y},p={x:point.x-a.x,y:point.y-a.y},det=x.x*y.y-x.y*y.x;
    return {x:(p.x*y.y-p.y*y.x)/det,y:(x.x*p.y-x.y*p.x)/det};
  }
  function zero(dial,p){return (Math.atan2(p.x-dial.center_x,-(p.y-dial.center_y))*180/Math.PI+360)%360;}
  function valid(dial){return Number.isFinite(dial.zero_angle_degrees)&&dial.zero_angle_degrees>=0&&dial.zero_angle_degrees<360&&dial.radius>=.15&&dial.radius<=.49&&[dial.center_x,dial.center_y].every(c=>Number.isFinite(c)&&c-dial.radius>=-1e-9&&c+dial.radius<=1+1e-9);}
  function scalePlan(cfg,index){
    const roi=cfg.rois[index];
    if(!roi||roi.kind!=="analog")throw new Error("Select a pointer dial.");
    const included=cfg.rois.filter(r=>r.include_in_reading!==false),digits=cfg.rois.map((r,i)=>r.kind!=="analog"&&r.include_in_reading!==false?i:-1).filter(i=>i>=0);
    const lastDigit=digits.at(-1);
    if(lastDigit===undefined)throw new Error("Add the number wheels before chaining decimal dials.");
    if(index<=lastDigit)throw new Error("Place the dial after the number wheels in reading order.");
    const baseDecimals=cfg.decimal_places-included.filter(r=>r.kind==="analog").length;
    if(baseDecimals<0)throw new Error("Check the counter's decimal places before adding dials.");
    const dials=cfg.rois.map((r,i)=>r.kind==="analog"&&i>lastDigit?i:-1).filter(i=>i>=0),ordinal=dials.indexOf(index)+1;
    const parent=ordinal>1?dials[ordinal-2]:lastDigit,places=baseDecimals+ordinal,step=10**-places;
    const pending=cfg.rois.slice(lastDigit+1,index).some(r=>r.kind==="analog"&&r.include_in_reading===false);
    const later=cfg.rois.slice(index+1).some(r=>r.include_in_reading!==false);
    const reason=pending?"Include the preceding dial first.":places>6?"The total supports up to six decimal places.":later&&roi.include_in_reading===false?"Include dials in reading order, from coarse to fine.":"";
    return {ordinal,parent,places,step,ratio:.1,can_include:!reason,reason};
  }
  function includePlan(cfg,index,confirmation){
    const roi=cfg.rois[index];
    if(!roi||roi.kind!=="analog"||roi.include_in_reading!==false)throw new Error("Select a preview-only dial first.");
    const plan=scalePlan(cfg,index),expected=plan.step;
    if(!plan.can_include)throw new Error(plan.reason);
    if(confirmation!=="previous/10"&&(!/^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?$/.test(String(confirmation))||Math.abs(Number(confirmation)-expected)>expected*1e-8))throw new Error(`Confirm ×0.1 of the preceding wheel or dial (${expected.toFixed(plan.places)} m³ per step).`);
    return {decimal_places:cfg.decimal_places+1,ignored_decimal_digits:0};
  }
  function removePlan(cfg,index){
    const roi=cfg.rois[index];
    if(!roi||roi.kind!=="analog"||roi.include_in_reading===false)throw new Error("Select an included dial.");
    if(cfg.rois.slice(index+1).some(r=>r.include_in_reading!==false)||cfg.decimal_places<1)throw new Error("Remove the finest included dial first.");
    return {decimal_places:cfg.decimal_places-1,ignored_decimal_digits:Math.min(cfg.ignored_decimal_digits||0,cfg.decimal_places-1)};
  }
  return {defaults,local,zero,valid,scalePlan,includePlan,removePlan};
});
