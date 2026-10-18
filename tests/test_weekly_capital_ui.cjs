const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function fixture({ready=true,blockedStorage=false}={}){
 const elements={},make=()=>({value:'',textContent:'',hidden:false,listeners:{},addEventListener(k,v){this.listeners[k]=v;},replaceChildren(){},createTHead(){return this;},createTBody(){return this;},insertRow(){return this;},insertCell(){return make();},appendChild(){}});
 const $=id=>elements[id]??=(make()),calls=[];let stored=null;
 const result={withinBudget:true,bandReached:true,rows:[{code:'1101',name:'fixture',current:10,target:10,change:0,side:'HOLD',reason:'續抱不動'}],invested:94000,cash:6000,fees:0,exposure:.94,previousDate:'2026-10-08',date:'2026-10-16',source:'reconstructed',snapshot:{schema:1,rebalance_date:'2026-10-16',capital:100000,holdings:[{code:'1101',shares:10}]}};
 const scope={document:{getElementById:$,createElement:make},localStorage:{getItem(){if(blockedStorage)throw Error('blocked');return stored;},setItem(k,v){if(blockedStorage)throw Error('blocked');stored=v;},removeItem(){stored=null;}},weeklyCapitalPlan(args){calls.push(args);if(!args.capital||!args.previousCapital)throw Error('兩個金額都必填');return result;},navigator:{clipboard:{writeText:async()=>{}}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../weekly_capital_ui.js'),'utf8'),scope);
 const api=scope.initWeeklyCapital({data:{sig_validated:true,previous_capital_signal:{rebalance_date:'2026-10-08'},capital_signal:{}},money:String,isReady:()=>ready});
 return {$,api,calls,result,setReady:v=>ready=v,stored:()=>stored,click:()=>$.call(null,'cap-apply').listeners.click()};
}
test('both manual fields are required without falling back to a hidden default',()=>{const f=fixture();f.$('cap-previous').value='100000';f.click();assert.equal(f.api.getTargets(),null);assert.match(f.$('weekly-capital-error').textContent,/必填/);assert.equal(f.stored(),null);});
test('valid manual fields produce targets and save only the theoretical snapshot',()=>{const f=fixture();f.$('cap-previous').value='100000';f.$('cap').value='150000';f.click();assert.equal(f.calls[0].previousCapital,100000);assert.equal(f.calls[0].capital,150000);assert.equal(f.api.getTargets()[0].quantity,10);assert.ok(f.stored());});
test('editing either amount invalidates the old order list immediately',()=>{const f=fixture();f.$('cap-previous').value=f.$('cap').value='100000';f.click();f.$('cap-previous').listeners.input();assert.equal(f.api.getTargets(),null);assert.equal(f.$('weekly-capital-results').hidden,true);});
test('failed or stale weekly signal blocks calculations and invalidates an existing list',()=>{const f=fixture({ready:false});f.$('cap-previous').value=f.$('cap').value='100000';f.click();assert.equal(f.calls.length,0);f.setReady(true);f.click();assert.ok(f.api.getTargets());f.setReady(false);f.api.validateFreshness();assert.equal(f.api.getTargets(),null);assert.equal(f.$('weekly-capital-results').hidden,true);});
test('blocked browser storage leaves this calculation usable and explains reconstruction',()=>{const f=fixture({blockedStorage:true});f.$('cap-previous').value=f.$('cap').value='100000';f.click();assert.ok(f.api.getTargets());assert.match(f.$('weekly-capital-error').textContent,/無法儲存/);});
test('over-budget blocked portfolio produces no executable targets or saved state',()=>{const f=fixture();f.result.withinBudget=false;f.$('cap-previous').value=f.$('cap').value='100000';f.click();assert.equal(f.api.getTargets(),null);assert.equal(f.stored(),null);});

const {weeklyCapitalReady}=require('../weekly_status.js');
const confirmed={calendar_years:['2026'],holidays:['2026-10-09','2026-10-26'],rebal_date:'2026-10-16',sig_date:'2026-10-15',price_basis_date:'2026-10-16',signal_mode:'CLOSE_SIMULATION',sig_validated:true,canonical_hash:'fixture'};
const readyStatus={state:'ready',rebalance_date:'2026-10-16',canonical_hash:'fixture'};
test('confirmed last-week close remains usable for Monday remainder orders',()=>assert.equal(weeklyCapitalReady(confirmed,'2026-10-19',readyStatus),true));
test('old plan is blocked on the next rebalance day',()=>assert.equal(weeklyCapitalReady(confirmed,'2026-10-23',readyStatus),false));
test('current retry state blocks previous plan reuse',()=>assert.equal(weeklyCapitalReady(confirmed,'2026-10-22',{state:'retrying',rebalance_date:'2026-10-23'}),false));
test('a plan older than one weekly cycle cannot be reused',()=>assert.equal(weeklyCapitalReady(confirmed,'2026-10-27',readyStatus),false));
test('unvalidated close is never usable for a remainder order',()=>assert.equal(weeklyCapitalReady({...confirmed,sig_validated:false},'2026-10-19',readyStatus),false));
