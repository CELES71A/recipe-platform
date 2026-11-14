/** DOM + real HTTP integration, NOT a rendered-browser or layout test.
 * Local dependency: npm ci --prefix tests/ui --ignore-scripts
 * Run: node scripts/test_ingredient_ui.mjs
 */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {spawn, execFileSync} from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = process.env.INGREDIENT_TEST_PYTHON || path.join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
const artifacts = path.join(root, 'data/stage3');
fs.mkdirSync(artifacts, {recursive: true});
const run = fs.mkdtempSync(path.join(artifacts, 'run-'));
const jsdomPackage = path.join(root, 'tests/ui/node_modules/jsdom');
if (!fs.existsSync(path.join(jsdomPackage, 'lib/api.js'))) {
  throw new Error('Install DOM test tools first: npm ci --prefix tests/ui --ignore-scripts');
}
for (const file of ['data/stage1_sample.db','data/stage1_no_match.db','data/stage1/acceptance_expected.json']) {
  if (!fs.existsSync(path.join(root,file))) throw new Error('Generate fixtures first: python scripts/prepare_ingredient_fixtures.py --recipes PATH --reviews PATH');
}
const {JSDOM} = await import(pathToFileURL(path.join(jsdomPackage, 'lib/api.js')));
const jsdomVersion = JSON.parse(fs.readFileSync(path.join(jsdomPackage, 'package.json'))).version;
const nativeFetch = globalThis.fetch;
const checks = [], requests = [], processes = [], gates = [], assetChecks = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let dom, activeBase, failNext, holdNext;
const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const backendFiles = ['backend/app/main.py','backend/app/database.py','backend/app/ingredient_matching.py','backend/app/ingredient_routes.py','scripts/test_ingredient_api.py'];
const backendHashes = Object.fromEntries(backendFiles.map(file => [file, hash(path.join(root, file))]));

const manifest = JSON.parse(execFileSync(python, ['-B','-c', String.raw`
from contextlib import closing
import hashlib,json,sqlite3,sys
from pathlib import Path
root=Path.cwd(); run=Path(sys.argv[1])
sources=[root/'data/stage1_sample.db',root/'data/stage1_no_match.db']
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
for source,name in ((sources[0],'baseline.db'),(sources[1],'no_match.db'),(sources[0],'unknown.db')):
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src:
        with closing(sqlite3.connect(run/name)) as dst: src.backup(dst)
with closing(sqlite3.connect(run/'unknown.db')) as db:
    with db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('DELETE FROM recipe_ingredients WHERE ingredient_id=3')
        db.execute('DELETE FROM ingredients WHERE id=3')
print(json.dumps({'source_hashes':hashes,'unknown_fixture':'ingredient 3 removed only in unknown.db'}))
`, run], {cwd:root, encoding:'utf8'}));

async function until(predicate, label, timeout=5000) {
  const deadline = Date.now()+timeout;
  while (Date.now()<deadline) { if (predicate()) return; await delay(10); }
  throw new Error(`Timed out: ${label}`);
}
async function start(name) {
  const port = await new Promise((resolve,reject) => {
    const server=net.createServer(); server.on('error',reject);
    server.listen(0,'127.0.0.1',()=>{const value=server.address().port;server.close(()=>resolve(value));});
  });
  const log=fs.openSync(path.join(run,`${name}_server.log`),'wx');
  const child=spawn(python,['-B','-m','uvicorn','app.main:app','--app-dir','backend','--host','127.0.0.1','--port',String(port),'--no-access-log'],{
    cwd:root,env:{...process.env,APP_DB_PATH:path.join(run,`${name}.db`),PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore',log,log],
  });
  fs.closeSync(log);
  const base=`http://127.0.0.1:${port}`;
  const record={child,base,name,pid:child.pid}; processes.push(record);
  for(let attempt=0;attempt<100;attempt++) {
    if(child.exitCode!=null) throw new Error(fs.readFileSync(path.join(run,`${name}_server.log`),'utf8'));
    try { const response=await nativeFetch(base+'/api/v1/health'); if(response.ok) return base; } catch {}
    await delay(50);
  }
  throw new Error(`Server did not start: ${name}`);
}
async function stop() {
  for (const gate of gates) gate.release();
  for (const {child} of processes) {
    if(child.exitCode==null) child.kill('SIGINT');
    await until(()=>child.exitCode!=null,'server shutdown').catch(()=>child.kill('SIGKILL'));
  }
}
async function check(name, callback) {
  try { await callback(); checks.push({name,status:'passed'}); console.log('PASS',name); }
  catch(error) { checks.push({name,status:'failed',error:error.stack}); throw error; }
}
function gate(matcher) {
  let release;
  const promise=new Promise(resolve=>{release=resolve;});
  const control={matcher,promise,release,started:false}; gates.push(control); holdNext=control; return control;
}
const q = id => document.querySelector(`#${id}`);
const submit = id => q(id).dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
const optionBusy = () => q('ingredient-options').getAttribute('aria-busy')==='true';
const matchBusy = () => q('ingredient-matches').getAttribute('aria-busy')==='true';
const text = id => q(id).textContent;
const cards = () => [...q('ingredient-matches').querySelectorAll('article')];
const recipe38 = () => q('ingredient-matches').querySelector('[data-recipe-id="38"]');
const count = () => Number(text('ingredient-selected-count'));
async function search(value) {
  q('ingredient-query').value=value;
  q('ingredient-query').dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  submit('ingredient-form');
  await until(()=>!optionBusy(),'options response');
  assert.equal(q('ingredient-options-error').hidden,true,text('ingredient-options-error'));
}
async function select(id, name) {
  await search(name);
  const input=q('ingredient-options').querySelector(`input[value="${id}"]`);
  assert.ok(input,`option ${name} exists`);
  if (!input.checked) input.click();
}
async function match() {
  submit('ingredient-match-form');
  await until(()=>!matchBusy(),'match response');
}
async function fullSelection() {
  if(!q('ingredient-clear').disabled) q('ingredient-clear').click();
  for(const [id,name] of [[1,'blueberries'],[2,'granulated sugar'],[3,'vanilla yogurt'],[4,'lemon juice']]) await select(id,name);
}

let failure;
try {
  const baseline=await start('baseline'), noMatch=await start('no_match'), unknown=await start('unknown');
  activeBase=baseline;
  let servedHTML;
  for(const [url,file,type] of [['/','index.html','text/html'],['/app.js','app.js','javascript'],
                              ['/ingredient-search.js','ingredient-search.js','javascript'],
                              ['/ingredient-search.css','ingredient-search.css','text/css']]) {
    const response=await nativeFetch(baseline+url);
    assert.equal(response.status,200);
    assert.ok(response.headers.get('content-type').includes(type));
    const content=await response.text();
    assert.equal(content,fs.readFileSync(path.join(root,'frontend',file),'utf8'));
    assetChecks.push({path:url,status:200,type:response.headers.get('content-type'),content_matches_file:true});
    if(url==='/') servedHTML=content;
  }
  dom=new JSDOM(servedHTML,{url:baseline,runScripts:'outside-only'});
  globalThis.window=dom.window; globalThis.document=dom.window.document; globalThis.FormData=dom.window.FormData;
  globalThis.alert=message=>{throw new Error(`Unexpected alert: ${message}`);};
  // jsdom has no browser modal/focus trap. This verifies content and callbacks only.
  dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;};
  globalThis.fetch=async (url,options={})=>{
    const base=activeBase, parsed=new URL(url,base);
    const record={base,path:parsed.pathname+parsed.search,method:options.method||'GET'};
    requests.push(record);
    if(failNext?.(parsed,options)) { failNext=null; record.simulated_network_error=true; throw new TypeError('simulated offline'); }
    const control=holdNext;
    if(control && !control.started && control.matcher(parsed,options)) {
      control.started=true; holdNext=null;
      const response=await nativeFetch(parsed,{...options,signal:undefined});
      record.status=response.status;
      await control.promise; return response;
    }
    const response=await nativeFetch(parsed,options); record.status=response.status; return response;
  };
  await import(pathToFileURL(path.join(root,'frontend/app.js')));
  await until(()=>text('result-count').includes('100'),'original home initialization');

  await check('navigation, empty selection and accessible native controls',async()=>{
    q('ingredient-nav').click();
    await until(()=>!optionBusy(),'initial options');
    assert.equal(q('browse').hidden,true); assert.equal(q('ingredient-search').hidden,false);
    await match(); assert.match(text('ingredient-match-error'),/至少选择一种/);
    assert.equal(q('ingredient-match-error').getAttribute('role'),'alert');
    assert.equal(q('ingredient-query').labels.length,1);
    assert.equal(q('ingredient-options').querySelector('input').type,'checkbox');
  });
  await check('ingredient pagination and retained selections across search',async()=>{
    await search('');
    const first=q('ingredient-options').querySelector('input'); const id=first.value;
    first.click(); assert.equal(count(),1);
    q('ingredient-next').click(); await until(()=>!optionBusy(),'options page 2');
    assert.match(text('ingredient-page'),/第 2/); assert.equal(count(),1);
    q('ingredient-previous').click(); await until(()=>!optionBusy(),'options page 1');
    assert.equal(q('ingredient-options').querySelector(`input[value="${id}"]`).checked,true);
    await search('blue'); assert.equal(count(),1);
    q('ingredient-clear').click(); assert.equal(count(),0); assert.equal(document.activeElement,q('ingredient-query'));
  });
  await check('recipe 38 complete match 100.0%, ordered cards and existing detail callback',async()=>{
    await fullSelection(); await match();
    assert.equal(count(),4); assert.match(recipe38().textContent,/100\.0%/);
    assert.match(recipe38().textContent,/无需补充食材/);
    const expected=JSON.parse(fs.readFileSync(path.join(root,'data/stage1/acceptance_expected.json'))).cases.full_38;
    assert.deepEqual(cards().map(card=>Number(card.dataset.recipeId)),expected.ordered_recipe_ids.slice(0,12));
    recipe38().querySelector('button').click();
    await until(()=>q('detail-dialog').open,'existing detail opened');
    assert.match(text('detail'),/Low-Fat Berry Blue Frozen Dessert/);
    assert.match(text('detail'),/blueberries/);
    q('close-detail').click(); assert.equal(q('detail-dialog').open,false);
    assert.equal(count(),4); assert.match(recipe38().textContent,/100\.0%/);
  });
  await check('match paging follows API order and preserves selection',async()=>{
    q('ingredient-match-next').click(); await until(()=>!matchBusy(),'match page 2');
    assert.deepEqual(cards().map(card=>Number(card.dataset.recipeId)),[126,41,71]);
    assert.equal(count(),4); assert.equal(q('ingredient-match-next').disabled,true);
    q('ingredient-match-previous').click(); await until(()=>!matchBusy(),'match page 1');
    assert.equal(cards()[0].dataset.recipeId,'38');
  });
  await check('recipe 38 partial match 50.0% and required ingredient details',async()=>{
    q('ingredient-selected').querySelector('[aria-label="移除 vanilla yogurt"]').click();
    q('ingredient-selected').querySelector('[aria-label="移除 lemon juice"]').click();
    assert.equal(cards().length,0); assert.equal(count(),2);
    await match(); assert.match(recipe38().textContent,/50\.0%/);
    const lists=recipe38().querySelectorAll('ul');
    assert.deepEqual([...lists[0].children].map(n=>n.textContent),['blueberries','granulated sugar']);
    assert.deepEqual([...lists[1].children].map(n=>n.textContent),['vanilla yogurt','lemon juice']);
    recipe38().querySelector('button').click(); await until(()=>q('detail-dialog').open,'partial detail');
    q('close-detail').click(); assert.equal(count(),2); assert.match(recipe38().textContent,/50\.0%/);
  });
  await check('loading state, disabled duplicate submit and response recovery',async()=>{
    const pending=gate(url=>url.pathname==='/api/v1/recipe-matches');
    submit('ingredient-match-form'); await until(()=>pending.started,'held match');
    assert.equal(matchBusy(),true); assert.equal(q('ingredient-match-submit').disabled,true);
    assert.match(text('ingredient-match-status'),/正在/); assert.equal(cards().length,0);
    pending.release(); await until(()=>!matchBusy(),'held match release');
    assert.match(recipe38().textContent,/50\.0%/);
  });
  await check('old ingredient response cannot overwrite a newer search',async()=>{
    const pending=gate(url=>url.pathname==='/api/v1/ingredients' && url.searchParams.get('q')==='milk');
    q('ingredient-query').value='milk'; submit('ingredient-form');
    await until(()=>pending.started,'held old lookup');
    await search('blue'); assert.match(text('ingredient-options'),/blueberries/);
    pending.release(); await delay(40);
    assert.match(text('ingredient-options'),/blueberries/); assert.doesNotMatch(text('ingredient-options'),/nonfat dry milk/);
  });
  await check('old match response cannot restore stale cards after selection changes',async()=>{
    await fullSelection();
    const pending=gate(url=>url.pathname==='/api/v1/recipe-matches');
    submit('ingredient-match-form'); await until(()=>pending.started,'held full match');
    q('ingredient-selected').querySelector('[aria-label="移除 vanilla yogurt"]').click();
    q('ingredient-selected').querySelector('[aria-label="移除 lemon juice"]').click();
    assert.equal(cards().length,0); assert.equal(matchBusy(),false);
    await match(); assert.match(recipe38().textContent,/50\.0%/);
    pending.release(); await delay(40);
    assert.equal(count(),2); assert.match(recipe38().textContent,/50\.0%/);
  });
  await check('ingredient network failure and retry',async()=>{
    failNext=url=>url.pathname==='/api/v1/ingredients';
    q('ingredient-query').value='blue'; submit('ingredient-form'); await until(()=>!optionBusy(),'lookup failure');
    assert.equal(q('ingredient-options-retry').hidden,false);
    assert.match(text('ingredient-options-error'),/请求失败/);
    q('ingredient-options-retry').click(); await until(()=>!optionBusy(),'lookup retry');
    assert.match(text('ingredient-options'),/blueberries/); assert.equal(count(),2);
  });
  await check('match and detail request failure, retained input and retries',async()=>{
    failNext=url=>url.pathname==='/api/v1/recipe-matches';
    await match(); assert.equal(count(),2); assert.equal(q('ingredient-match-retry').hidden,false);
    assert.match(text('ingredient-match-error'),/请求失败/);
    q('ingredient-match-retry').click(); await until(()=>!matchBusy(),'match retry');
    assert.match(recipe38().textContent,/50\.0%/);
    failNext=url=>url.pathname==='/api/v1/recipes/38';
    recipe38().querySelector('button').click();
    await until(()=>!recipe38().querySelector('[role=alert]').hidden,'detail failure');
    assert.match(recipe38().querySelector('[role=alert]').textContent,/请求失败/);
    recipe38().querySelector('button').click(); await until(()=>q('detail-dialog').open,'detail retry'); q('close-detail').click();
  });
  await check('valid ingredient with no published candidates on independent database',async()=>{
    activeBase=noMatch; q('ingredient-clear').click(); await select(3,'vanilla yogurt'); await match();
    assert.equal(cards().length,0); assert.match(text('ingredient-match-status'),/没有匹配的已发布食谱/);
    assert.equal(q('ingredient-match-error').hidden,true); assert.equal(count(),1);
  });
  await check('unknown ingredient from real backend 422 and recovery',async()=>{
    activeBase=unknown; await match();
    assert.match(text('ingredient-match-error'),/未知食材 ID：3/);
    assert.equal(q('ingredient-match-retry').hidden,false); assert.equal(count(),1);
    activeBase=baseline; q('ingredient-match-retry').click(); await until(()=>!matchBusy(),'unknown retry');
    assert.match(recipe38().textContent,/25\.0%/);
  });
  await check('empty lookup and focus after removing final selected tag',async()=>{
    await search('no-such-ingredient-for-stage3');
    assert.match(text('ingredient-options-status'),/没有找到食材/);
    q('ingredient-selected').querySelector('button').click();
    assert.equal(count(),0); assert.equal(document.activeElement,q('ingredient-query'));
  });
  await check('non-terminating coverage displays one decimal: recipe 52 at 66.7%',async()=>{
    await select(30,'sugar'); await select(90,'instant coffee'); await match();
    const card=q('ingredient-matches').querySelector('[data-recipe-id="52"]');
    assert.match(card.textContent,/66\.7%/);
    assert.match(card.querySelectorAll('ul')[1].textContent,/nonfat dry milk solid/);
  });
  await check('original home search, detail and return to ingredient page',async()=>{
    await fullSelection(); await match();
    q('home').click(); assert.equal(q('ingredient-search').hidden,true);
    q('search-form').querySelector('[name=q]').value='chicken'; submit('search-form');
    await until(()=>text('result-count').includes('8'),'original chicken search');
    assert.equal(q('recipes').children.length,8);
    q('recipes').querySelector('button').click(); await until(()=>q('detail-dialog').open,'home recipe detail');
    assert.match(text('detail'),/Warm Chicken A La King/); q('close-detail').click();
    q('ingredient-nav').click(); assert.equal(count(),4); assert.match(recipe38().textContent,/100\.0%/);
  });
} catch(error) {
  failure=error; console.error(error.stack);
} finally {
  globalThis.fetch=nativeFetch;
  for(const gate of gates) gate.release();
  dom?.window.close();
  await stop();
  for(const [file,value] of Object.entries(backendHashes)) assert.equal(hash(path.join(root,file)),value,`backend preserved: ${file}`);
  for(const [file,value] of Object.entries(manifest.source_hashes)) assert.equal(hash(file),value,`Stage 1 database preserved: ${file}`);
  const summary={run_directory:run,test_kind:'jsdom DOM + real HTTP; not browser rendering',jsdom_version:jsdomVersion,
    checks,asset_checks:assetChecks,request_count:requests.length,
    simulated_network_failures:requests.filter(r=>r.simulated_network_error).length,
    real_http_responses:requests.filter(r=>r.status!=null).length,failures:failure?1:0,
    servers:processes.map(({name,pid,base,child})=>({name,pid,base,exit_code:child.exitCode})),
    browser_acceptance:'pending: no browser connected; layout, native keyboard traversal and modal focus require real browser',
    backend_and_source_databases_unchanged:true};
  fs.writeFileSync(path.join(run,'results.json'),JSON.stringify(summary,null,2)+'\n');
  fs.writeFileSync(path.join(run,'http_requests.json'),JSON.stringify(requests,null,2)+'\n');
  fs.writeFileSync(path.join(artifacts,'latest_results.json'),JSON.stringify(summary,null,2)+'\n');
  console.log('ARTIFACTS',run);
  if(failure) process.exitCode=1;
}
