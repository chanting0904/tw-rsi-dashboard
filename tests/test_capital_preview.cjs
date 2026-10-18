const test = require('node:test');
const assert = require('node:assert/strict');
const {previewAllocation} = require('../capital_preview.js');
const targets = [{code:'2330', target_weight:.3, price:1000}, {code:'1303', target_weight:.3, price:100},
  {code:'3443', target_weight:.05, price:8000}, {code:'2409', target_weight:.3, price:35}];
test('different capitals conserve money and use whole shares', () => {
  for (const capital of [100000,200000,1000000]) {
    const result = previewAllocation(targets, capital);
    assert.ok(result.cash >= 0);
    assert.ok(Object.values(result.quantities).every(Number.isInteger));
    assert.ok(Math.abs(result.cash + result.invested + result.fees - capital) < 1e-7);
    assert.ok(result.exposure <= .95 + 1e-10);
  }
});
test('high price under one target share is reported', () => assert.ok(previewAllocation(targets, 100000).unaffordable.includes('3443')));
test('fees never overdraw even at a 100% target', () => {
  const result = previewAllocation([{code:'2330', target_weight:1, price:100}], 1000);
  assert.equal(result.quantities['2330'], 9);
  assert.ok(result.cash > 0);
});

test('commission minimum applies once to each nonzero stock order', () => {
  const result = previewAllocation([{code:'x', target_weight:.5,price:1},{code:'y',target_weight:.45,price:1}],100);
  assert.equal(result.fees,2);
  assert.equal(result.details.reduce((s,r)=>s+r.commission,0),2);
  assert.ok(result.exposure<=.95+1e-10);
});

test('zero share orders never incur minimum commission', () => {
  const result = previewAllocation([{code:'x',target_weight:.95,price:1000}],10);
  assert.equal(result.fees,0);
  assert.equal(result.cash,10);
});
test('invalid or duplicate targets rejected', () => {
  assert.throws(() => previewAllocation([{code:'x', target_weight:1.1, price:10}], 100));
  assert.throws(() => previewAllocation([{code:'x', target_weight:.1, price:10}, {code:'x', target_weight:.1, price:10}], 100));
  assert.throws(() => previewAllocation(targets, Infinity));
  assert.throws(() => previewAllocation([{code:'x', target_weight:NaN, price:10}], 100));
});
test('missing quotes get zero shares and a warning', () => {
  const result = previewAllocation([{code:'x', target_weight:.95, price:null}], 100000);
  assert.equal(result.quantities.x, 0);
  assert.deepEqual(result.missing, ['x']);
});
test('95 percent ceiling includes fees and survives rounded target weights', () => {
  for (const capital of [100000,200000,1000000]) {
    for (const weight of [.95,.95004,1]) {
      const result = previewAllocation([{code:'x', target_weight:weight, price:1}],capital);
      assert.ok(result.exposure <= .95 + 1e-10);
      assert.ok(Math.abs(result.cash + result.invested + result.fees - capital) < 1e-7);
      assert.equal(result.details[0].reason,'exposure_limit');
    }
  }
});

test('oversized capital or unsafe quantities fail fast without a rounding loop',()=>{
 assert.throws(()=>previewAllocation([{code:'2330',target_weight:.95,price:.01}],1e100));
 assert.throws(()=>previewAllocation([{code:'2330',target_weight:.95,price:1e-10}],1e12));
});

test('rounding repair near the safe integer boundary finishes with safe quantities',()=>{
 const {spawnSync}=require('node:child_process');const modulePath=require.resolve('../capital_preview.js');
 const code='const {previewAllocation}=require('+JSON.stringify(modulePath)+');const p=previewAllocation([{code:"1101",target_weight:.5,price:.000105},{code:"1102",target_weight:.45,price:450000000000}],1000000000000);if(!Object.values(p.quantities).every(Number.isSafeInteger))process.exit(2);';
 const r=spawnSync(process.execPath,['-e',code],{timeout:3000,encoding:'utf8'});assert.equal(r.error,undefined);assert.equal(r.status,0,r.stderr);
});
