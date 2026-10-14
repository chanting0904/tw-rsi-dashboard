const test = require('node:test');
const assert = require('node:assert/strict');
const {taipeiDate, weeklyReadiness} = require('../weekly_status.js');
const data = {calendar_years: ['2026'], holidays: ['2026-10-09'],
  rebal_date: '2026-10-16', sig_date: '2026-10-15', price_basis_date: '2026-10-15',
  signal_mode: 'PRE_CLOSE_PLAN', sig_validated: true};
test('Taipei rolls into next day before UTC does', () => assert.equal(taipeiDate(new Date('2026-10-15T23:00:00Z')), '2026-10-16'));
test('correct prior-close plan is ready', () => assert.equal(weeklyReadiness(data, '2026-10-16').state, 'ready'));
test('old page is waiting before Friday', () => assert.equal(weeklyReadiness({...data, rebal_date: '2026-10-07'}, '2026-10-14').title, '等待本週資料'));
test('old green page cannot remain ready in next week', () => assert.equal(weeklyReadiness(data, '2026-10-19').state, 'waiting'));
test('missing validation is never ready', () => assert.equal(weeklyReadiness({...data, sig_validated: false}, '2026-10-16').state, 'waiting'));
test('wrong price basis is never ready', () => assert.equal(weeklyReadiness({...data, price_basis_date: '2026-10-14'}, '2026-10-16').state, 'waiting'));
test('current failure takes precedence over matching date', () => assert.equal(weeklyReadiness(data, '2026-10-16', {rebalance_date:'2026-10-16', state:'failed'}).state, 'failed'));
test('old failure cannot override this week', () => assert.equal(weeklyReadiness(data, '2026-10-16', {rebalance_date:'2026-10-08', state:'failed'}).state, 'ready'));
test('ready status with old page asks for refresh', () => assert.equal(weeklyReadiness({...data, rebal_date:'2026-10-07'}, '2026-10-16', {rebalance_date:'2026-10-16', state:'ready'}).title, '本週計畫已更新，請重新整理'));
test('retrying is not ready', () => assert.match(weeklyReadiness(data, '2026-10-16', {rebalance_date:'2026-10-16', state:'retrying', attempt:2}).title, /重試/));
test('Friday holiday plans Thursday', () => assert.equal(weeklyReadiness(data, '2026-10-08').expected, '2026-10-08'));
test('missing annual calendar never assumes ready', () => assert.equal(weeklyReadiness(data, '2027-01-08').state, 'failed'));
test('fully closed week has no plan', () => assert.equal(weeklyReadiness({...data, holidays: ['2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16']}, '2026-10-16').title, '本週休市，沒有調倉計畫'));
