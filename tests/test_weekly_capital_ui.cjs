const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function fixture({ready=true,blockedStorage=false,data=null,stored:initial=null}={}){
 const elements={},created=[],make=()=>{const node={value:'',textContent:'',hidden:false,className:'',children:[],listeners:{},addEventListener(k,v){this.listeners[k]=v;},replaceChildren(){this.children=[];},createTHead(){const child=make();this.children.push(child);return child;},createTBody(){const child=make();this.children.push(child);return child;},insertRow(){const child=make();this.children.push(child);return child;},insertCell(){const child=make();this.children.push(child);return child;},appendChild(child){this.children.push(child);},append(...children){this.children.push(...children);}};created.push(node);return node;};
 const $=id=>elements[id]??=(make()),calls=[];let stored=initial;
 const result={withinBudget:true,bandReached:true,rows:[{code:'1101',name:'fixture',current:10,target:10,change:0,side:'HOLD',reason:'續抱不動'}],invested:94000,cash:6000,fees:0,exposure:.94,previousDate:'2026-10-08',date:'2026-10-16',source:'reconstructed',snapshot:{schema:1,rebalance_date:'2026-10-16',capital:100000,holdings:[{code:'1101',shares:10}]}};
 const scope={document:{getElementById:$,createElement:make},localStorage:{getItem(){if(blockedStorage)throw Error('blocked');return stored;},setItem(k,v){if(blockedStorage)throw Error('blocked');stored=v;},removeItem(){stored=null;}},weeklyCapitalPlan(args){calls.push(args);if(!args.capital||!args.previousCapital)throw Error('兩個金額都必填');return result;},navigator:{clipboard:{writeText:async()=>{}}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../weekly_capital_ui.js'),'utf8'),scope);
 const api=scope.initWeeklyCapital({data:data||{sig_validated:true,previous_capital_signal:{rebalance_date:'2026-10-08'},capital_signal:{}},money:String,isReady:()=>ready});
 return {$,api,calls,result,created,setReady:v=>ready=v,stored:()=>stored,click:()=>$.call(null,'cap-apply').listeners.click()};
}
test('partial sells have a distinct visual marker while exits do not',()=>{const f=fixture();f.result.rows=[
 {code:'6214',name:'精誠',current:26,target:12,change:-14,side:'SELL',reason:'減碼'},
 {code:'6672',name:'騰輝電子',current:7,target:0,change:-7,side:'SELL',reason:'退出'}];
 f.$('cap-previous').value=f.$('cap').value='100000';f.click();
 const marked=f.created.filter(n=>n.className==='is-reduction');assert.equal(marked.length,1);
 assert.equal(marked[0].children[0].children[0].textContent,'6214');
});
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

test('positive pending rights are shown separately so the capital total reconciles',()=>{const f=fixture();f.result.reservedAssets=118;f.$('cap-previous').value=f.$('cap').value='100000';f.click();assert.match(f.$('preview-result-note').textContent,/待領權益 118/);});

test('every amount tried for a week is kept so next week finds the one actually used',()=>{
 const week={rebalance_date:'2026-10-16',actions:[]},f=fixture({data:{sig_validated:true,previous_capital_signal:{rebalance_date:'2026-10-08'},capital_signal:week}});
 for(const capital of [100037,90000]){f.result.snapshot={schema:1,rebalance_date:'2026-10-16',capital,holdings:[{code:'1101',shares:capital===100037?23:20}]};f.$('cap-previous').value='100000';f.$('cap').value=String(capital);f.click();}
 const next=fixture({stored:f.stored(),data:{sig_validated:true,previous_capital_signal:week,capital_signal:{rebalance_date:'2026-10-23'}}});
 next.$('cap-previous').value='100037';next.$('cap').value='100810';next.click();
 assert.equal(next.calls[0].snapshot.holdings[0].shares,23);
});
test('a missing record is called out instead of silently estimated',()=>{const f=fixture();f.$('cap-previous').value=f.$('cap').value='100000';f.click();assert.equal(f.calls[0].snapshot,null);assert.match(f.$('weekly-capital-basis').textContent,/⚠ 注意/);});
