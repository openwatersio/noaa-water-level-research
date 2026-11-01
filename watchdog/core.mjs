const REPO = 'openwatersio/noaa-water-level-research';
const ACTIVE = ['queued', 'in_progress', 'waiting', 'requested', 'pending'];
const FINAL = new Set(['published', 'published_acquisition_failed']);

export function slotAt(value) {
  const at = new Date(value);
  if (!Number.isFinite(+at)) throw Error('invalid UTC clock');
  const slot = new Date(at); slot.setUTCMinutes(50, 0, 0);
  if (slot > at) slot.setUTCHours(slot.getUTCHours() - 1);
  return slot.toISOString().replace('.000Z', 'Z');
}

function slotFile(slot) {
  if (slotAt(slot) !== slot) throw Error('invalid slot');
  return 'scheduling/slots/' + slot.replaceAll(/[-:]/g, '') + '.json';
}

function canonical(value) {
  if (Array.isArray(value)) return JSON.stringify(value.map(v=>JSON.parse(canonical(v))));
  if (value && typeof value === 'object') return JSON.stringify(Object.fromEntries(
    Object.keys(value).sort().map(k=>[k, JSON.parse(canonical(value[k]))])));
  return JSON.stringify(value);
}

async function bytes(response) {
  if (!response.ok) throw Error('public archive HTTP ' + response.status);
  const reader = response.body.getReader(), chunks = []; let total = 0;
  while (true) {
    const {value, done} = await reader.read(); if (done) break;
    total += value.length;
    if (total > 40 * 1024 * 1024) { await reader.cancel(); throw Error('archive exceeds download bound'); }
    chunks.push(value);
  }
  const result = new Uint8Array(total); let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}

export class GitHub {
  constructor(token, transport = (...args)=>fetch(...args)) {
    if (!token) throw Error('GITHUB_TOKEN is required');
    this.token = token; this.transport = transport;
  }
  async api(path, {method='GET', body, missing=false, raw=false}={}) {
    const response = await this.transport(`https://api.github.com/repos/${REPO}/${path}`, {
      method, headers:{Authorization:'Bearer '+this.token, 'User-Agent':'noaa-scheduling-watchdog',
        Accept:raw ? 'application/vnd.github.raw+json' : 'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28', ...(body ? {'Content-Type':'application/json'} : {})},
      body:body ? JSON.stringify(body) : undefined, signal:AbortSignal.timeout(10000), redirect:'manual',
    });
    if (missing && response.status === 404) return null;
    if (!response.ok) throw Error('GitHub HTTP '+response.status+' on '+path.split('?')[0]);
    return raw ? JSON.parse(await response.text()) : response.status === 204 ? null : response.json();
  }
  async enabled() { return (await this.api('actions/variables/COLLECTION_ENABLED')).value === 'true'; }
  async main() { return (await this.api('git/ref/heads/main')).object.sha; }
  async snapshot() { return (await this.api('git/ref/heads/data')).object.sha; }
  async file(path, snapshot) {
    return this.api('contents/'+path+'?ref='+snapshot, {missing:true,raw:true});
  }
  async claim(slot, snapshot) { return this.file(slotFile(slot), snapshot); }
  async busy() {
    for (const workflow of ['collect.yml','reconcile.yml']) {
      for (const status of ACTIVE) {
        for (let page=1; page<=100; page++) {
          const runs = (await this.api(`actions/workflows/${workflow}/runs?status=${status}&per_page=100&page=${page}`)).workflow_runs;
          if (runs.some(r=>r.status !== 'completed')) return true;
          if (runs.length < 100) break;
          if (page === 100) throw Error('writer pagination bound exceeded');
        }
      }
    }
    return false;
  }
  async dispatch(inputs) {
    const response = await this.transport(`https://api.github.com/repos/${REPO}/actions/workflows/collect.yml/dispatches`, {
      method:'POST', headers:{Authorization:'Bearer '+this.token, 'User-Agent':'noaa-scheduling-watchdog',
        Accept:'application/vnd.github+json', 'Content-Type':'application/json', 'X-GitHub-Api-Version':'2022-11-28'},
      body:JSON.stringify({ref:'main',inputs}), signal:AbortSignal.timeout(10000), redirect:'manual',
    });
    return response.status;
  }
  async dispatchedRun(intent) {
    if (!intent.dispatchId) return null;
    // GitHub clocks have second precision and may differ from Cloudflare's clock.
    const lower = new Date(Date.parse(intent.dispatchedAt)-60000).toISOString().replace(/\.\d{3}Z$/,'Z');
    const created = encodeURIComponent('>='+lower);
    for (let page=1; page<=10; page++) {
      const runs = (await this.api(`actions/workflows/collect.yml/runs?event=workflow_dispatch&created=${created}&per_page=100&page=${page}`)).workflow_runs;
      const found = runs.find(r=>r.display_title === 'NOAA receipts watchdog:'+intent.dispatchId);
      if (found) return found;
      if (runs.length < 100) return null;
    }
    throw Error('dispatch lookup pagination bound exceeded');
  }
  async publicFile(tag, name) {
    if (!/^[a-zA-Z0-9-]+$/.test(tag) || !/^[a-zA-Z0-9_.-]+$/.test(name)) throw Error('invalid asset path');
    return bytes(await this.transport(`https://github.com/${REPO}/releases/download/${tag}/${name}`,
      {signal:AbortSignal.timeout(20000)}));
  }
  async assets(releaseId) {
    const assets=[];
    for (let page=1; page<=100; page++) {
      const batch=await this.api(`releases/${releaseId}/assets?per_page=100&page=${page}`);
      assets.push(...batch);
      if (batch.length<100) return assets;
    }
    throw Error('Release asset pagination bound exceeded');
  }
  async metadata(tag, assets, name, expected) {
    const candidates=assets.filter(a=>a.state==='uploaded' && (a.name===name || a.name.startsWith(name+'.retry-')));
    if (!candidates.length) throw Error('missing uploaded metadata '+name);
    let previous;
    for (const asset of candidates) {
      const text=new TextDecoder().decode(await this.publicFile(tag,asset.name));
      if ((previous!==undefined && text!==previous) || canonical(JSON.parse(text))!==canonical(expected)) {
        throw Error('public metadata '+name+' differs from Git evidence or retries');
      }
      previous=text;
    }
  }
  async publication(claim, snapshot, at) {
    if (!/^[a-zA-Z0-9-]+$/.test(claim.batchId) || !/^\d+$/.test(claim.runId)) throw Error('invalid claim identity');
    const run = await this.api('actions/runs/'+claim.runId);
    if (run.head_sha !== claim.codeCommit || run.event !== claim.event) throw Error('Actions provenance differs');
    const clocks = {actionsRunId:run.id,actionsCreatedAt:run.created_at,actionsStartedAt:run.run_started_at,
      actionsStatus:run.status,actionsConclusion:run.conclusion,event:claim.event,scheduling:claim.scheduling,
      codeCommit:claim.codeCommit,startedAt:claim.startedAt};
    const terminal = await this.file('runs/'+claim.batchId+'.json',snapshot);
    if (!terminal) return {...clocks,state:'awaiting_terminal'};
    if (canonical(terminal.context) !== canonical(Object.fromEntries(Object.entries(claim).filter(([k])=> !['state','startedAt'].includes(k))))) {
      throw Error('terminal context differs from durable claim');
    }
    if (!terminal.archive) return {...clocks,state:'publication_failed'};
    if (run.status !== 'completed') return {...clocks,state:'awaiting_actions_completion'};
    const catalog = await this.file('catalog/'+claim.batchId+'.json',snapshot);
    if (!catalog) return {...clocks,state:'awaiting_catalog'};
    if (canonical(catalog.context) !== canonical(terminal.context) || catalog.tag !== terminal.archive.tag ||
      catalog.releaseId !== terminal.archive.releaseId) throw Error('catalog identity differs');
    const release = await this.api('releases/'+catalog.releaseId);
    if (release.draft || !release.immutable || release.tag_name !== catalog.tag) throw Error('archive is not the claimed immutable Release');
    const assets=await this.assets(catalog.releaseId);
    for (const [name,expected] of [['archive.json',catalog],['context.json',catalog.context]]) {
      await this.metadata(catalog.tag,assets,name,expected);
    }
    if (terminal.state==='complete' && (!catalog.bundles.length || !Object.keys(catalog.files).length)) {
      throw Error('empty successful acquisition archive');
    }
    for (const bundle of catalog.bundles) {
      if (!assets.some(a=>a.id===bundle.asset.id && a.name===bundle.asset.name && a.size===bundle.asset.size && a.state==='uploaded')) {
        throw Error('bundle asset identity differs from immutable Release');
      }
      const blob = await this.publicFile(catalog.tag,bundle.asset.name);
      const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256',blob))].map(b=>b.toString(16).padStart(2,'0')).join('');
      if (blob.length !== bundle.asset.size || hash !== bundle.sha256) throw Error('archive bundle hash or length differs');
    }
    const complete = terminal.state === 'complete' && terminal.exitCode === 0 && terminal.tick?.exitCode === 0 &&
      catalog.completion === 'complete' && run.conclusion === 'success';
    return {...clocks,state:complete ? 'published' : 'published_acquisition_failed',release:catalog.tag,
      batchId:claim.batchId,verificationStartedAt:at,verifiedAt:new Date().toISOString(),
      dataCommit:snapshot,bundleCount:catalog.bundles.length,
      verification:'Public context/catalog equality and stored bundle SHA-256/length; original-file unpacking remains an independent acceptance check.'};
  }
}

export class Watchdog {
  constructor(storage,github,env,clock=()=>new Date().toISOString()) {
    this.storage=storage; this.github=github; this.env=env; this.clock=clock;
  }
  async closedSlot(snapshot,at,enabled) {
    const coverageFrom=await this.storage.transaction(async txn=>{
      const existing=await txn.get('coverageFrom');
      if (existing) return existing;
      const firstFullWindow=slotAt(new Date(Date.parse(slotAt(at))+3600000).toISOString());
      await txn.put('coverageFrom',firstFullWindow); return firstFullWindow;
    });
    const current=slotAt(at), slot=slotAt(new Date(Date.parse(current)-3600000).toISOString());
    // A partially observed deployment window and a still-publishing writer cannot prove a missed window.
    if (!enabled || slot<coverageFrom || Date.parse(at)<Date.parse(current)+900000) return null;
    const claim=await this.github.claim(slot,snapshot), key='gap/'+slot, previous=await this.storage.get(key);
    if (claim) {
      if (claim.scheduling?.slot!==slot) throw Error('closed-window claim identity differs');
      if (previous && !previous.resolvedAt) await this.storage.put(key,{...previous,resolvedAt:at,resolutionBatchId:claim.batchId});
      return null;
    }
    if (previous?.resolvedAt) return null;
    const gap={...previous,slot,detectedAt:previous?.detectedAt||at,lastCheckedAt:at,dataCommit:snapshot,
      state:'no_durable_claim_at_check'};
    await this.storage.put(key,gap);
    return gap;
  }
  async observe(row,snapshot,at) {
    const claim = await this.github.claim(row.slot,snapshot);
    let observation;
    if (claim) {
      if (claim.scheduling?.slot !== row.slot) throw Error('claim intended slot differs');
      observation = await this.github.publication(claim,snapshot,at);
      observation.externalDispatchMatched=Boolean(row.dispatchId && claim.event==='workflow_dispatch' && claim.scheduling.dispatchId===row.dispatchId);
    }
    else {
      const run = await this.github.dispatchedRun(row);
      observation = run ? {state:run.status === 'completed' ? 'completed_without_claim' : 'awaiting_start',
        actionsRunId:run.id,actionsStatus:run.status,actionsConclusion:run.conclusion,
        actionsCreatedAt:run.created_at,actionsStartedAt:run.run_started_at} : {state:'awaiting_start'};
    }
    return this.storage.transaction(async txn=>{
      const current=await txn.get('slot/'+row.slot);
      if (FINAL.has(current.state)) return current;
      const updated={...current,...observation,checkedAt:at};
      delete updated.error;
      await txn.put('slot/'+row.slot,updated); return updated;
    });
  }
  async tick(at,cronAt) {
    const slot=slotAt(at); let result={at,cronScheduledAt:cronAt,slot};
    try {
      if (!/^[a-f0-9]{40}$/.test(this.env.REVIEWED_COMMIT)) throw Error('exact REVIEWED_COMMIT is required');
      if (new Date(cronAt)>new Date(at) || !Number.isFinite(+new Date(cronAt))) throw Error('invalid cron clock');
      const enabled=await this.github.enabled(), head=await this.github.main();
      if (!enabled) {
        const pausedAt=await this.storage.transaction(async txn=>{
          const since=await txn.get('pausedAt');
          if (since) return since;
          await txn.put('pausedAt',at); return at;
        });
        result.state='paused'; result.pausedSince=pausedAt;
        if (Date.parse(at)-Date.parse(pausedAt)>5400000) {
          result.state='attention_required'; result.error='collection_pause_observed_over_90_minutes';
        }
      } else if (await this.storage.get('pausedAt')) await this.storage.put('pausedAt',null);
      if (head!==this.env.REVIEWED_COMMIT) {
        result.state='attention_required'; result.error='unreviewed_main'; result.actualMainCommit=head;
      }
      const snapshot=await this.github.snapshot();
      const gap=await this.closedSlot(snapshot,at,enabled);
      let row=await this.storage.get('slot/'+slot);
      const claim=await this.github.claim(slot,snapshot);
      if (claim && !row) {
        row=await this.storage.transaction(async txn=>{
          const existing=await txn.get('slot/'+slot);
          if (existing) return existing;
          const observed={slot,state:'started',observedAt:at};
          await txn.put('slot/'+slot,observed); return observed;
        });
      }
      if (!row && enabled && head===this.env.REVIEWED_COMMIT) {
        if (await this.github.busy()) result.state='writer_busy';
        else {
          const reservedAt=this.clock();
          if (slotAt(reservedAt) !== slot) {
            result.state=gap ? 'attention_required' : 'expired_before_dispatch';
            if (gap) { result.error='closed_slot_without_durable_claim'; result.gap=gap; }
            await this.storage.put('lastTick',result); return result;
          }
          const intent={slot,dispatchId:crypto.randomUUID(),dispatchedAt:reservedAt,cronScheduledAt:cronAt,
            expectedCommit:this.env.REVIEWED_COMMIT,state:'awaiting_start',dispatchState:'ambiguous'};
          const reserved=await this.storage.transaction(async txn=>{
            if (await txn.get('slot/'+slot)) return false;
            await txn.put('slot/'+slot,intent); return true;
          });
          if (reserved) {
            let status=null;
            try {
              status=await this.github.dispatch({slot,dispatch_id:intent.dispatchId,dispatched_at:reservedAt,
                cron_scheduled_at:cronAt,expected_commit:this.env.REVIEWED_COMMIT});
            } catch { /* A lost response cannot prove that dispatch failed. */ }
            intent.httpStatus=status;
            intent.dispatchState=status === 204 ? 'accepted' : status && status>=400 && status<500 ? 'rejected' : 'ambiguous';
            await this.storage.transaction(async txn=>{
              const current=await txn.get('slot/'+slot);
              await txn.put('slot/'+slot,{...current,httpStatus:intent.httpStatus,dispatchState:intent.dispatchState});
            });
            result.state='dispatched'; result.dispatchState=intent.dispatchState;
            if (intent.dispatchState!=='accepted') {
              result.state='attention_required'; result.error='dispatch_'+intent.dispatchState+': HTTP '+status;
            }
          } else result.state='already_reserved';
        }
      } else if (!result.state) result.state='already_claimed_or_reserved';
      if (!claim && row?.dispatchState==='rejected') {
        result.state='attention_required'; result.error='dispatch_rejected: HTTP '+row.httpStatus;
      }
      const rows=[...(await this.storage.list({prefix:'slot/',reverse:true,limit:72})).values()]
        .filter(r=>!FINAL.has(r.state)).sort((a,b)=>(a.checkedAt||a.observedAt||a.dispatchedAt).localeCompare(b.checkedAt||b.observedAt||b.dispatchedAt));
      const current=rows.find(r=>r.slot===slot);
      const candidate=current && (!current.checkedAt || Date.parse(at)-Date.parse(current.checkedAt)>=600000) ? current : rows[0];
      if (candidate) {
        try { result.observation=await this.observe(candidate,snapshot,at); }
        catch (error) {
          await this.storage.transaction(async txn=>{
            const latest=await txn.get('slot/'+candidate.slot);
            if (!FINAL.has(latest.state)) await txn.put('slot/'+candidate.slot,{...latest,checkedAt:at,
              state:'verification_failed',error:String(error.message).slice(0,400),
              lastError:{at,message:String(error.message).slice(0,400)}});
          });
          throw error;
        }
      }
      if (gap) {
        result.state='attention_required'; result.error='closed_slot_without_durable_claim'; result.gap=gap;
      }
    } catch (error) {
      result.state='attention_required'; result.error=String(error.message).slice(0,400);
    }
    await this.storage.put('lastTick',result);
    console.log(JSON.stringify(result));
    return result;
  }
  async status() {
    return {lastTick:await this.storage.get('lastTick'),slots:[...(await this.storage.list({prefix:'slot/',reverse:true,limit:72})).values()],
      coverageFrom:await this.storage.get('coverageFrom'),claimGaps:[...(await this.storage.list({prefix:'gap/',reverse:true,limit:72})).values()],
      acceptance:'Deployment and dispatch are not migration acceptance. Require a prospective 48-hour evidence review; local overlap remains active.'};
  }
}
