import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Watchdog, GitHub, slotAt } from './core.mjs';

class Storage {
  values = new Map(); queue = Promise.resolve();
  async get(k) { return structuredClone(this.values.get(k)); }
  async put(k,v) { this.values.set(k, structuredClone(v)); }
  async list({prefix='', limit=1000, reverse=false}={}) {
    let keys = [...this.values.keys()].filter(k=>k.startsWith(prefix)).sort();
    if(reverse) keys.reverse();
    return new Map(keys.slice(0,limit).map(k=>[k,structuredClone(this.values.get(k))]));
  }
  async transaction(fn) {
    const result = this.queue.then(()=>fn(this)); this.queue = result.catch(()=>{}); return result;
  }
}
const at = '2026-11-01T12:50:01Z', slot = '2026-11-01T12:50:00Z';
function setup(overrides={}) {
  const sent = [], store = new Storage();
  const github = {
    enabled:async()=>true, main:async()=> 'a'.repeat(40), busy:async()=>false,
    snapshot:async()=> 'b'.repeat(40), claim:async()=>null,
    dispatchedRun:async()=>null, publication:async()=>({state:'published'}),
    dispatch:async input=> { sent.push(input); return 204; }, ...overrides,
  };
  return {store, github, sent, dog:new Watchdog(store,github,{REVIEWED_COMMIT:'a'.repeat(40)},()=>at)};
}
test('minute-50 boundary never selects a future slot',()=>{
  assert.equal(slotAt('2026-11-01T12:49:59Z'),'2026-11-01T11:50:00Z');
  assert.equal(slotAt(at),slot);
});
test('repeated concurrent ticks reserve one dispatch and retain actual intent',async()=>{
  const s=setup(); await Promise.all([s.dog.tick(at,slot),s.dog.tick(at,slot)]);
  await s.dog.tick('2026-11-01T12:55:01Z','2026-11-01T12:55:00Z');
  assert.equal(s.sent.length,1); assert.equal(s.sent[0].slot,slot);
  assert.equal(s.sent[0].dispatched_at,at); assert.equal(s.sent[0].cron_scheduled_at,slot);
  assert.equal(s.sent[0].expected_commit,'a'.repeat(40));
  assert.equal((await s.store.get('slot/'+slot)).state,'awaiting_start');
});
test('pause, active writers, unreviewed main and unavailable controls fail closed',async()=>{
  for (const change of [{enabled:async()=>false},{busy:async()=>true},
    {main:async()=> 'c'.repeat(40)}, {enabled:async()=>{throw Error('control unavailable');}}]) {
    const s=setup(change); await s.dog.tick(at,slot); assert.equal(s.sent.length,0);
    assert.notEqual((await s.store.get('lastTick')).state,'dispatched');
  }
});
test('a lost POST response or 5xx never causes redispatch after object reconstruction',async()=>{
  for(const response of [()=>{throw Error('timeout');},()=>503]) {
    let count=0; const s=setup({dispatch:async()=>{count++; return response();}});
    await s.dog.tick(at,slot);
    const dog=new Watchdog(s.store,s.github,{REVIEWED_COMMIT:'a'.repeat(40)},()=>at);
    await dog.tick('2026-11-01T12:55:01Z','2026-11-01T12:55:00Z');
    assert.equal(count,1); assert.equal((await s.store.get('slot/'+slot)).dispatchState,'ambiguous');
  }
});
test('reservation failure prevents dispatch',async()=>{
  const s=setup(); s.store.transaction=async()=>{throw Error('storage unavailable');};
  await s.dog.tick(at,slot); assert.equal(s.sent.length,0);
});
test('native claim suppresses external dispatch and publication remains distinct from acceptance',async()=>{
  const s=setup({claim:async()=>({batchId:'native',scheduling:{slot}}), publication:async()=>({state:'awaiting_terminal'})});
  await s.dog.tick(at,slot); assert.equal(s.sent.length,0);
  assert.equal((await s.store.get('slot/'+slot)).state,'awaiting_terminal');
  s.github.publication=async()=>({state:'published',release:'receipts-native',verifiedAt:at});
  await s.dog.tick('2026-11-01T12:55:01Z','2026-11-01T12:55:00Z');
  assert.equal((await s.store.get('slot/'+slot)).state,'published');
  assert.equal((await s.store.get('slot/'+slot)).release,'receipts-native');
});
test('failed acquisition consumes the slot without being called successful',async()=>{
  const s=setup({claim:async()=>({batchId:'partial',scheduling:{slot}}),publication:async()=>({state:'published_acquisition_failed'})});
  await s.dog.tick(at,slot); await s.dog.tick('2026-11-01T12:55:01Z',slot);
  assert.equal(s.sent.length,0); assert.equal((await s.store.get('slot/'+slot)).state,'published_acquisition_failed');
});
test('accepted dispatch with no durable claim discloses completed Actions without acquisition',async()=>{
  const s=setup(); await s.dog.tick(at,slot);
  s.github.dispatchedRun=async()=>({id:42,status:'completed',conclusion:'success',created_at:at,run_started_at:at});
  await s.dog.tick('2026-11-01T12:55:01Z',slot);
  const result=await s.store.get('slot/'+slot);
  assert.equal(result.state,'completed_without_claim'); assert.equal(result.actionsRunId,42);
  assert.equal(s.sent.length,1);
});
test('an old unresolved slot is observed after hour rollover without backfill',async()=>{
  const s=setup(); await s.dog.tick(at,slot);
  s.github.claim=async value=> value===slot ? {batchId:'delayed',scheduling:{slot}} : null;
  s.dog.clock=()=> '2026-11-01T13:50:01Z';
  await s.dog.tick('2026-11-01T13:50:01Z','2026-11-01T13:50:00Z');
  assert.equal(s.sent.length,2); assert.equal(s.sent[1].slot,'2026-11-01T13:50:00Z');
  await s.dog.tick('2026-11-01T13:55:01Z','2026-11-01T13:55:00Z');
  assert.equal((await s.store.get('slot/'+slot)).state,'published');
});

test('GitHub active-writer queries include every active state and paginate',async()=>{
  const urls=[];
  const gh=new GitHub('secret',async(url,options)=>{
    urls.push(url); assert.equal(options.headers.Authorization,'Bearer secret');
    const parsed=new URL(url);
    const page=parsed.searchParams.get('page'), status=parsed.searchParams.get('status');
    return Response.json({workflow_runs:page==='1' && status==='pending' ? Array(100).fill({status:'completed'}) :
      page==='2' && status==='pending' ? [{status:'pending'}] : []});
  });
  assert.equal(await gh.busy(),true);
  assert.ok(urls.some(u=>u.includes('page=2')));
  for(const status of ['queued','in_progress','waiting','requested','pending']) {
    const calls=[];
    const api=new GitHub('secret',async url=>{ calls.push(url); return Response.json({workflow_runs:[]}); });
    assert.equal(await api.busy(),false);
    assert.ok(calls.some(u=>u.includes('status='+status)));
    assert.ok(calls.some(u=>u.includes('reconcile.yml')));
  }
});

test('GitHub publication requires terminal, catalog, immutable release, matching context and bundle bytes',async()=>{
  const body=new TextEncoder().encode('original compressed archive bytes');
  const digest=Buffer.from(await crypto.subtle.digest('SHA-256',body)).toString('hex');
  const claim={batchId:'batch',runId:'42',codeCommit:'a'.repeat(40),event:'workflow_dispatch',scheduling:{slot}};
  const catalog={context:claim,tag:'receipts-batch',releaseId:9,completion:'complete',files:{'ticks/t.json':{}},
    bundles:[{sha256:digest,asset:{id:11,name:'bundle.tar.gz',size:body.length}}]};
  let terminal={context:claim,state:'complete',exitCode:0,tick:{exitCode:0},archive:{tag:'receipts-batch',releaseId:9}};
  let immutable=true, corrupt=false, missingCatalog=false, conflictingRetry=false;
  const gh=new GitHub('secret',async(url,options)=>{
    const path=new URL(url).pathname;
    if(path.endsWith('/runs/42')) return Response.json({id:42,head_sha:'a'.repeat(40),status:'completed',conclusion:'success',event:'workflow_dispatch'});
    if(path.endsWith('/contents/runs/batch.json')) return terminal ? new Response(JSON.stringify(terminal)) : new Response('',{status:404});
    if(path.endsWith('/contents/catalog/batch.json')) return missingCatalog ? new Response('',{status:404}) : new Response(JSON.stringify(catalog));
    if(path.endsWith('/releases/9/assets')) return Response.json([
      {id:1,name:'context.json',state:'starter',size:0},
      {id:2,name:'context.json.retry-one',state:'uploaded'},
      {id:3,name:'context.json.retry-two',state:'uploaded'},
      {id:4,name:'archive.json.retry-one',state:'uploaded'},
      {id:11,name:'bundle.tar.gz',size:body.length,state:'uploaded'}]);
    if(path.endsWith('/releases/9')) return Response.json({id:9,tag_name:'receipts-batch',immutable,draft:false});
    if(path.includes('/releases/download/')) {
      assert.equal(options?.headers?.Authorization,undefined);
      if(path.endsWith('/archive.json')) return new Response('',{status:404});
      if(path.includes('/archive.json.retry-')) return new Response(JSON.stringify(catalog));
      if(path.endsWith('/context.json')) return new Response('',{status:404});
      if(path.includes('/context.json.retry-')) return new Response(JSON.stringify(conflictingRetry && path.endsWith('two') ? {bad:true} : claim));
      return new Response(corrupt ? 'bad bytes' : body);
    }
    throw Error('unexpected URL '+url);
  });
  assert.equal((await gh.publication(claim,'b'.repeat(40),at)).state,'published');
  terminal=null;
  assert.equal((await gh.publication(claim,'b'.repeat(40),at)).state,'awaiting_terminal');
  terminal={context:claim,state:'publication_failed',exitCode:1};
  assert.equal((await gh.publication(claim,'b'.repeat(40),at)).state,'publication_failed');
  terminal={context:claim,state:'complete',exitCode:0,tick:{exitCode:0},archive:{tag:'receipts-batch',releaseId:9}};
  missingCatalog=true;
  assert.equal((await gh.publication(claim,'b'.repeat(40),at)).state,'awaiting_catalog');
  missingCatalog=false; conflictingRetry=true;
  await assert.rejects(gh.publication(claim,'b'.repeat(40),at),/metadata|differs/);
  conflictingRetry=false; immutable=false;
  await assert.rejects(gh.publication(claim,'b'.repeat(40),at),/immutable/);
  immutable=true; corrupt=true;
  await assert.rejects(gh.publication(claim,'b'.repeat(40),at),/hash|length/);
  corrupt=false; terminal.state='acquisition_failed'; terminal.exitCode=1; terminal.tick.exitCode=1;
  assert.equal((await gh.publication(claim,'b'.repeat(40),at)).state,'published_acquisition_failed');
  terminal.state='interrupted'; terminal.tick=null; catalog.completion='interrupted'; catalog.bundles=[]; catalog.files={};
  assert.equal((await gh.publication(claim,'b'.repeat(40),at)).state,'published_acquisition_failed');
});

test('dispatch records the actual reservation clock after slow preflight and expires across boundaries',async()=>{
  const s=setup(); s.dog.clock=()=> '2026-11-01T12:51:20Z';
  await s.dog.tick(at,slot);
  assert.equal(s.sent[0].dispatched_at,'2026-11-01T12:51:20Z');
  const expired=setup(); expired.dog.clock=()=> '2026-11-01T13:50:00Z';
  await expired.dog.tick(at,slot);
  assert.equal(expired.sent.length,0);
  assert.equal((await expired.store.get('lastTick')).state,'expired_before_dispatch');
});
test('a claim filed under the wrong intended slot cannot become publication proof',async()=>{
  const s=setup({claim:async()=>({batchId:'wrong',scheduling:{slot:'2026-11-01T11:50:00Z'}})});
  await s.dog.tick(at,slot);
  assert.equal((await s.store.get('lastTick')).state,'attention_required');
  assert.equal((await s.store.get('slot/'+slot)).state,'verification_failed');
});

test('completed run with a stale snapshot remains observable when a fresh claim appears',async()=>{
  const s=setup(); await s.dog.tick(at,slot);
  s.github.dispatchedRun=async()=>({id:42,status:'completed',conclusion:'success'});
  await s.dog.tick('2026-11-01T12:55:01Z',slot);
  s.github.claim=async()=>({batchId:'newly-visible',scheduling:{slot}});
  await s.dog.tick('2026-11-01T13:00:01Z',slot);
  assert.equal((await s.store.get('slot/'+slot)).state,'published');
});
test('concurrent observation preserves a confirmed dispatch response',async()=>{
  let releasePost, postStarted, releaseObservation, observationStarted;
  const started=new Promise(r=>postStarted=r), post=new Promise(r=>releasePost=r);
  const observing=new Promise(r=>observationStarted=r), observation=new Promise(r=>releaseObservation=r);
  let reads=0;
  const s=setup({dispatch:async()=>{postStarted(); await post; return 204;},
    dispatchedRun:async()=>{if(++reads===1){observationStarted(); await observation;} return null;}});
  const first=s.dog.tick(at,slot); await started;
  const second=s.dog.tick(at,slot); await observing;
  releasePost(); await first; releaseObservation(); await second;
  const row=await s.store.get('slot/'+slot);
  assert.equal(row.dispatchState,'accepted'); assert.equal(row.httpStatus,204);
});
test('definitive dispatch rejection is actionable and never automatically retried',async()=>{
  const s=setup({dispatch:async()=>403});
  const first=await s.dog.tick(at,slot);
  assert.equal(first.state,'attention_required');
  assert.equal((await s.store.get('slot/'+slot)).dispatchState,'rejected');
  await s.dog.tick('2026-11-01T12:55:01Z',slot);
  assert.equal((await s.store.get('lastTick')).state,'attention_required');
});

test('dispatch lookup includes a same-second run despite reservation milliseconds and small clock skew',async()=>{
  const id='5a858471-9466-4e66-b76c-dea8d0485550';
  const gh=new GitHub('secret',async url=>{
    const filter=new URL(url).searchParams.get('created');
    assert.ok(Date.parse(filter.slice(2))<=Date.parse('2026-11-01T12:50:01Z'));
    return Response.json({workflow_runs:[{id:42,display_title:'NOAA receipts watchdog:'+id}]});
  });
  assert.equal((await gh.dispatchedRun({dispatchId:id,dispatchedAt:'2026-11-01T12:50:01.400Z'})).id,42);
});

test('pin drift and a pause observed for more than ninety minutes are actionable',async()=>{
  const drift=setup({main:async()=> 'c'.repeat(40)});
  assert.equal((await drift.dog.tick(at,slot)).state,'attention_required');
  const paused=setup({enabled:async()=>false});
  assert.equal((await paused.dog.tick(at,slot)).state,'paused');
  assert.equal((await paused.dog.tick('2026-11-01T14:21:01Z','2026-11-01T14:20:00Z')).state,'attention_required');
  assert.equal(paused.sent.length,0);
});
test('a successful recheck clears the active verification error but keeps its evidence',async()=>{
  const s=setup({claim:async()=>({batchId:'native',scheduling:{slot}}),publication:async()=>{throw Error('temporary archive 503');}});
  await s.dog.tick(at,slot);
  s.github.publication=async()=>({state:'published'});
  await s.dog.tick('2026-11-01T12:55:01Z',slot);
  const row=await s.store.get('slot/'+slot);
  assert.equal(row.state,'published'); assert.equal(row.error,undefined);
  assert.equal(row.lastError.message,'temporary archive 503');
});

test('a fully observed closed window without a claim is actionable even when dispatch was accepted or writers stayed busy',async()=>{
  for(const busy of [false,true]) {
    const s=setup({busy:async()=>busy});
    await s.dog.tick(at,slot);
    s.dog.clock=()=> '2026-11-01T13:50:01Z';
    await s.dog.tick('2026-11-01T13:50:01Z','2026-11-01T13:50:00Z');
    s.dog.clock=()=> '2026-11-01T15:05:01Z';
    const result=await s.dog.tick('2026-11-01T15:05:01Z','2026-11-01T15:05:00Z');
    assert.equal(result.state,'attention_required');
    assert.equal(result.error,'closed_slot_without_durable_claim');
    const gap=await s.store.get('gap/2026-11-01T13:50:00Z');
    assert.equal(gap.slot,'2026-11-01T13:50:00Z');
    assert.equal(gap.dataCommit,'b'.repeat(40));
    const sent=s.sent.length;
    s.github.claim=async value=>value==='2026-11-01T13:50:00Z' ? {batchId:'delayed-publication',scheduling:{slot:value}} : null;
    await s.dog.tick('2026-11-01T15:10:01Z','2026-11-01T15:10:00Z');
    const recovered=await s.store.get('gap/2026-11-01T13:50:00Z');
    assert.equal(recovered.detectedAt,gap.detectedAt);
    assert.equal(recovered.resolvedAt,'2026-11-01T15:10:01Z');
    assert.equal(s.sent.length,sent);
  }
});

test('native winner publication cannot count as proof of an external dispatch',async()=>{
  const s=setup(); await s.dog.tick(at,slot);
  s.github.claim=async()=>({batchId:'native-winner',event:'schedule',scheduling:{slot,source:'schedule'}});
  await s.dog.tick('2026-11-01T12:55:01Z',slot);
  const result=await s.store.get('slot/'+slot);
  assert.equal(result.state,'published'); assert.equal(result.dispatchState,'accepted');
  assert.equal(result.externalDispatchMatched,false);
});
