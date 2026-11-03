/* Local diagnostic export only. No credentials, publication or ledger writes. */
"use strict";
var TestBundle = (()=>{
  function serialize(example,label="") {
    label=String(label).trim();
    if(!example?.images?.original?.base64 || !example.calibration || typeof example.would_accept!=="boolean")throw new Error("Run an OCR test before exporting an example.");
    if(label && !/^[0-9]{1,9}(?:\.[0-9]{1,6})?$/.test(label))throw new Error("Use a nonnegative reading with a dot and at most six decimal places.");
    return JSON.stringify({schema_version:1,kind:"waterwatch_ocr_test",collected_at:example.collected_at||new Date().toISOString(),
      raw_value:example.raw_value,confidence:example.confidence,reason:example.reason,would_accept:example.would_accept,
      details:example.details,calibration:example.calibration,images:example.images,human_label:label||null},null,2);
  }
  return {serialize};
})();
if(typeof module!=="undefined" && module.exports)module.exports=TestBundle;
