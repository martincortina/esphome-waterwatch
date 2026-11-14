(function(root,factory){const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;else root.DialTools=api;})(typeof globalThis!=="undefined"?globalThis:this,function(){
  "use strict";
  function defaults(){return {center_x:.5,center_y:.5,radius:.45,zero_angle_degrees:0,clockwise:true,pointer_color:"red"};}
  function local(roi,point,corners){
    const [a,b,,d]=corners,x={x:b.x-a.x,y:b.y-a.y},y={x:d.x-a.x,y:d.y-a.y},p={x:point.x-a.x,y:point.y-a.y},det=x.x*y.y-x.y*y.x;
    return {x:(p.x*y.y-p.y*y.x)/det,y:(x.x*p.y-x.y*p.x)/det};
  }
  function zero(dial,p){return (Math.atan2(p.x-dial.center_x,-(p.y-dial.center_y))*180/Math.PI+360)%360;}
  function valid(dial){return Number.isFinite(dial.zero_angle_degrees)&&dial.zero_angle_degrees>=0&&dial.zero_angle_degrees<360&&dial.radius>=.15&&dial.radius<=.49&&[dial.center_x,dial.center_y].every(c=>Number.isFinite(c)&&c-dial.radius>=-1e-9&&c+dial.radius<=1+1e-9);}
  function includePlan(cfg,index,step){
    const roi=cfg.rois[index],expected=10**-(cfg.decimal_places+1);
    if(!roi||roi.include_in_reading!==false)throw new Error("Select a preview-only dial first.");
    if(cfg.rois.slice(index+1).some(r=>r.include_in_reading!==false))throw new Error("Add the dial after the other reading regions.");
    if(cfg.decimal_places>=6)throw new Error("At most six decimal places are supported.");
    if(!/^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?$/.test(String(step))||Math.abs(Number(step)-expected)>expected*1e-8)throw new Error(`Confirm ${expected.toFixed(cfg.decimal_places+1)} m³ per numbered step to add the next decimal.`);
    return {decimal_places:cfg.decimal_places+1,ignored_decimal_digits:0};
  }
  return {defaults,local,zero,valid,includePlan};
});
