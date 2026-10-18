const test=require('node:test'),assert=require('node:assert/strict');
const {parseHoldings,bandPrice,planOrders}=require('../order_planner.js');
const quotes={'2033':{name:'佳大',price:59.8,date:'2026-10-16',tradable:true},'1303':{name:'南亞',price:307,tradable:true},'2892':{name:'第一金',price:41.4,tradable:true}};
test('holdings accept shares, optional names, commas and explicit empty portfolio',()=>{
 assert.deepEqual(parseHoldings('2033 佳大 1,000\n1303,30'),[{code:'2033',name:'佳大',shares:1000},{code:'1303',name:'',shares:30}]);
 assert.deepEqual(parseHoldings('空手'),[]);
});
test('blank, duplicates, negative, fractional and ambiguous quantities fail',()=>{
 for(const text of ['', '2033 -1','2033 1.5','2033 1 000','2033 100\n2033 20','2033 1,00','2033 9007199254740992'])assert.throws(()=>parseHoldings(text));
});
test('limit-style estimates follow ordinary stock ticks',()=>{
 assert.equal(bandPrice(40.6,'BUY'),44.65);assert.equal(bandPrice(40.6,'SELL'),36.55);
 assert.equal(bandPrice(49.95,'BUY'),54.9);assert.equal(bandPrice(null,'BUY'),null);
});
test('differences use personal holdings, never model trade labels',()=>{
 const r=planOrders({targets:[{code:'2033',quantity:85},{code:'1303',quantity:24}],holdings:parseHoldings('2033 100\n1303 30\n2892 80'),quotes,stress:false});
 assert.deepEqual(r.rows.map(x=>[x.code,x.side,x.change]),[['1303','SELL',-6],['2033','SELL',-15],['2892','SELL',-80]]);
 assert.equal(r.rows.find(x=>x.code==='2892').target,0);
});
test('one commission minimum per actual delta; sales include stock tax',()=>{
 const r=planOrders({targets:[{code:'2033',quantity:2}],holdings:parseHoldings('2033 1\n2892 1'),quotes,stress:false});
 assert.equal(r.buyCost,60.8);assert.equal(r.sellNet,41.4-1-41.4*.003);
});
test('unfilled sale proceeds never make current buys affordable',()=>{
 const r=planOrders({targets:[{code:'2033',quantity:100}],holdings:parseHoldings('1303 30'),quotes,cash:0,stress:false});
 assert.equal(r.canAffordNow,false);assert.ok(r.shortageNow>0);assert.ok(r.afterSalesBalance>0);
});
test('no cash means theoretical quantities, not a passed funds check',()=>{
 const r=planOrders({targets:[{code:'2033',quantity:1}],holdings:[],quotes});
 assert.equal(r.canAffordNow,false);assert.equal(r.shortageNow,null);assert.equal(r.afterSalesBalance,null);
});
test('custom order prices override stress estimates without changing targets',()=>{
 const r=planOrders({targets:[{code:'2033',quantity:85}],holdings:parseHoldings('2033 80'),quotes,cash:1000,prices:{'2033':70}});
 assert.equal(r.rows[0].change,5);assert.equal(r.buyCost,351);assert.ok(r.canAffordNow);
});
test('missing or restricted securities do not silently fund buys',()=>{
 const r=planOrders({targets:[{code:'2033',quantity:1}],holdings:[{code:'3089',shares:10}],quotes,cash:0});
 assert.equal(r.complete,false);assert.equal(r.sellNet,0);assert.equal(r.afterSalesBalance,null);
 assert.ok(r.rows.find(x=>x.code==='3089').issues.length);
});
test('negative cash, invalid override and unsafe target quantities are blocked',()=>{
 assert.throws(()=>planOrders({targets:[],holdings:[],cash:-1}));
 assert.throws(()=>planOrders({targets:[{code:'2033',quantity:.5}],holdings:[]}));
 const r=planOrders({targets:[{code:'2033',quantity:1}],holdings:[],quotes,prices:{'2033':null}});assert.equal(r.complete,false);
});
test('unchanged shares need no order or fee',()=>{
 const r=planOrders({targets:[{code:'2033',quantity:100}],holdings:parseHoldings('2033 100'),quotes,cash:0});
 assert.equal(r.rows[0].side,'HOLD');assert.equal(r.fees,0);assert.equal(r.buyCost,0);
});

test('same-day settled net budget needs only total capital',()=>{
 const r=planOrders({targets:[{code:'2033',quantity:100}],holdings:parseHoldings('1303 30'),quotes,capital:10000,stress:false});
 assert.equal(r.currentValue,9210);assert.equal(r.netCash,790);assert.ok(r.buyCost>r.netCash);assert.equal(r.withinCapital,true);
 assert.ok(Math.abs(r.budgetBalance-(10000-r.currentValue+r.sellNet-r.buyCost))<1e-9);
});
test('order prices plus all actual costs cannot pass an excessive total budget',()=>{
 const r=planOrders({targets:[{code:'2033',quantity:200}],holdings:[],quotes,capital:10000,stress:false});
 assert.equal(r.withinCapital,false);assert.ok(r.budgetBalance<0);
});
test('held positions remain in capital budget and fees can exceed a tight capital',()=>{
 const r=planOrders({targets:[{code:'2033',quantity:100}],holdings:parseHoldings('2033 90'),quotes,capital:5980,stress:false});
 assert.equal(r.withinCapital,false);assert.ok(r.budgetBalance<0);assert.equal(r.rows[0].change,10);
});
test('missing valuation even for unchanged shares prevents a budget pass',()=>{
 const r=planOrders({targets:[{code:'2033',quantity:10}],holdings:parseHoldings('2033 10'),capital:10000});
 assert.equal(r.complete,true);assert.equal(r.budgetComplete,false);assert.equal(r.withinCapital,false);assert.deepEqual(r.valuationMissing,['2033']);
});
test('holdings exceeding entered total capital are rejected as inconsistent',()=>{
 const r=planOrders({targets:[{code:'2033',quantity:10}],holdings:parseHoldings('2033 100'),quotes,capital:100});
 assert.ok(r.netCash<0);assert.equal(r.withinCapital,false);assert.throws(()=>planOrders({targets:[],holdings:[],capital:-1}));
});
