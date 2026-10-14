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
  }
});
test('high price under one target share is reported', () => assert.ok(previewAllocation(targets, 100000).unaffordable.includes('3443')));
test('fees never overdraw even at a 100% target', () => {
  const result = previewAllocation([{code:'2330', target_weight:1, price:100}], 1000);
  assert.equal(result.quantities['2330'], 9);
  assert.ok(result.cash > 0);
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
