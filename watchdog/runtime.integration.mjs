import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Miniflare, Response as RuntimeResponse, convertV4MiniflareOptions } from 'miniflare';
import { slotAt } from './core.mjs';

const directory=dirname(fileURLToPath(import.meta.url));
const persistence=await mkdtemp(join(tmpdir(),'noaa-watchdog-runtime-'));
const bundle=join(persistence,'bundle');
execFileSync(process.execPath,[join(directory,'node_modules/wrangler/bin/wrangler.js'),'deploy','--dry-run','--outdir',bundle],
  {cwd:directory,stdio:'inherit',env:{...process.env,WRANGLER_SEND_METRICS:'false',WRANGLER_LOG_PATH:join(persistence,'wrangler.log')}});
const dispatched=[];
let publish=false, changedMain=false, context, catalog, terminal, assets, body;
const options=convertV4MiniflareOptions({name:'noaa-watchdog-test',modules:true,
  modulesRoot:bundle,scriptPath:join(bundle,'worker.js'),compatibilityDate:'2026-10-30',
  durableObjects:{WATCHDOG:{className:'SchedulingWatchdog',useSQLite:true}},
  bindings:{WATCHDOG_ENABLED:'true',REVIEWED_COMMIT:'a'.repeat(40),GITHUB_TOKEN:'synthetic-not-a-secret'},
  outboundService:async request=>{
    const url=new URL(request.url), path=url.pathname;
    const json=value=>RuntimeResponse.json(value);
    if(url.hostname==='github.com') {
      assert.equal(request.headers.get('Authorization'),null);
      if(path.endsWith('/archive.json')) return json(catalog);
      if(path.endsWith('/context.json')) return json(context);
      return new RuntimeResponse(body);
    }
    assert.equal(url.hostname,'api.github.com');
    assert.equal(request.headers.get('Authorization'),'Bearer synthetic-not-a-secret');
    if(path.endsWith('/git/ref/heads/data')) return json({object:{sha:'b'.repeat(40)}});
    if(path.includes('/contents/scheduling/slots/')) return publish ? json({...context,state:'started',startedAt:context.createdAt}) : new RuntimeResponse('',{status:404});
    if(path.endsWith('/actions/variables/COLLECTION_ENABLED')) return json({value:'true'});
    if(path.endsWith('/git/ref/heads/main')) return json({object:{sha:(changedMain?'c':'a').repeat(40)}});
    if(path.endsWith('/actions/runs/42')) return json({id:42,head_sha:'a'.repeat(40),event:context.event,status:'completed',conclusion:'success'});
    if(path.endsWith('/contents/runs/scale.json')) return json(terminal);
    if(path.endsWith('/contents/catalog/scale.json')) return json(catalog);
    if(path.endsWith('/releases/9')) return json({id:9,tag_name:'receipts-scale',immutable:true,draft:false});
    if(path.endsWith('/releases/9/assets')) return json(assets);
    if(path.endsWith('/runs')) return json({workflow_runs:[]});
    if(path.endsWith('/collect.yml/dispatches')) {
      assert.equal(request.method,'POST');
      const intent=await request.json();
      assert.equal(intent.ref,'main'); assert.equal(intent.inputs.expected_commit,'a'.repeat(40));
      dispatched.push(intent.inputs); return new RuntimeResponse(null,{status:204});
    }
    throw Error('unexpected outbound request '+url);
  }});
options.resourcePersistencePath=persistence;
let mf;
async function scheduled() {
  const when=Date.now()-1000, worker=await mf.getWorker();
  const result=await worker.scheduled({scheduledTime:when,cron:'*/5 * * * *'});
  return {result,when};
}
async function status() { return (await mf.dispatchFetch('https://watchdog/status')).json(); }
try {
  mf=new Miniflare(options);
  assert.equal((await mf.dispatchFetch('https://watchdog/status',{method:'POST'})).status,404);
  assert.equal((await mf.dispatchFetch('https://watchdog/tick')).status,404);
  for(let tries=0; !dispatched.length && tries<3; tries++) {
    const {result,when}=await scheduled(); assert.equal(result.outcome,'ok');
    assert.equal((await status()).lastTick.cronScheduledAt,new Date(when).toISOString());
  }
  assert.equal(dispatched.length,1);
  const intent=dispatched[0];
  await mf.dispose(); mf=new Miniflare(options);
  const restored=await status();
  assert.equal(restored.slots.find(r=>r.slot===intent.slot).dispatchState,'accepted');
  assert.equal(restored.enabled,true);
  await scheduled();
  assert.equal(dispatched.filter(i=>i.slot===intent.slot).length,1);
  assert.equal(new Set(dispatched.map(i=>i.slot)).size,dispatched.length);

  body=randomBytes(32*1024*1024);
  const hash=createHash('sha256').update(body).digest('hex');
  context={batchId:'scale',origin:'github',codeCommit:'a'.repeat(40),runId:'42',event:'schedule',
    createdAt:new Date().toISOString(),scheduling:{slot:slotAt(new Date().toISOString()),source:'schedule'}};
  catalog={context,tag:'receipts-scale',releaseId:9,completion:'complete',files:{'ticks/scale.json':{sha256:hash,bytes:body.length}},
    bundles:[0,1].map(i=>({sha256:hash,asset:{id:11+i,name:'bundle-'+i+'.tar.gz',size:body.length}}))};
  terminal={context,state:'complete',exitCode:0,tick:{exitCode:0},archive:{tag:'receipts-scale',releaseId:9}};
  assets=[{id:1,name:'archive.json',state:'uploaded'},{id:2,name:'context.json',state:'uploaded'},
    ...catalog.bundles.map(b=>({...b.asset,state:'uploaded'}))];
  publish=true;
  const namespace=await mf.getDurableObjectNamespace('WATCHDOG');
  const stub=namespace.get(namespace.idFromName('scale-verification'));
  const started=performance.now();
  const verified=await(await stub.fetch('https://internal/tick',{method:'POST',body:JSON.stringify({
    at:context.createdAt,cronAt:context.createdAt})})).json();
  assert.notEqual(verified.state,'attention_required',verified.error);
  assert.equal((await(await stub.fetch('https://internal/status')).json()).slots.find(r=>r.slot===context.scheduling.slot).state,'published');
  console.log('Workers verified two 32 MiB stored-asset fixtures in '+Math.round(performance.now()-started)+' ms.');
  changedMain=true;
  const failed=await scheduled(); assert.notEqual(failed.result.outcome,'ok');
  assert.equal((await status()).lastTick.error,'unreviewed_main');
  console.log('Workers runtime: configured scheduled handler and waitUntil; isolated outbound fetch/dispatch; read-only public routes; SQLite reservation survives restart; publication and pin-drift failure propagation.');
} finally {
  if(mf) await mf.dispose();
  await rm(persistence,{recursive:true,force:true});
}
