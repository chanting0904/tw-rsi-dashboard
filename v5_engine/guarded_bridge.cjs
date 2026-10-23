/* Shared browser/server allocation policy. Read JSON lines; never access network. */
'use strict';
const readline=require('node:readline');
require('../capital_preview.js');
globalThis.previewAllocation=require('../capital_preview.js').previewAllocation;
const {weeklyCapitalPlan}=require('../weekly_capital.js');
readline.createInterface({input:process.stdin}).on('line',line=>{
 try{console.log(JSON.stringify({ok:true,plan:weeklyCapitalPlan(JSON.parse(line))}));}
 catch(error){console.log(JSON.stringify({ok:false,error:error.message}));}
});
