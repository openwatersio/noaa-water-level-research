import { DurableObject } from 'cloudflare:workers';
import { GitHub, Watchdog } from './core.mjs';

export class SchedulingWatchdog extends DurableObject {
  async fetch(request) {
    const dog = new Watchdog(this.ctx.storage, new GitHub(this.env.GITHUB_TOKEN), this.env);
    if (request.method === 'GET') return Response.json({...await dog.status(),
      enabled:this.env.WATCHDOG_ENABLED === 'true',reviewedCommit:this.env.REVIEWED_COMMIT});
    const {at, cronAt} = await request.json();
    return Response.json(await dog.tick(at, cronAt));
  }
}

export default {
  async scheduled(event, env, ctx) {
    if (env.WATCHDOG_ENABLED !== 'true') { console.log('watchdog_disabled'); return; }
    const stub = env.WATCHDOG.getByName('noaa-hourly-v1');
    ctx.waitUntil(stub.fetch('https://internal/tick', {
      method:'POST', body:JSON.stringify({at:new Date().toISOString(), cronAt:new Date(event.scheduledTime).toISOString()}),
    }).then(async response=>{
      if (!response.ok) throw Error('watchdog object HTTP '+response.status);
      const result = await response.json();
      if (result.state === 'attention_required') throw Error(result.error);
    }));
  },
  async fetch(request, env) {
    if (request.method !== 'GET' || new URL(request.url).pathname !== '/status') return new Response('Not found', {status:404});
    if (!env.GITHUB_TOKEN) return Response.json({state:'unconfigured',enabled:env.WATCHDOG_ENABLED === 'true'}, {status:503});
    const response = await env.WATCHDOG.getByName('noaa-hourly-v1').fetch('https://internal/status');
    return new Response(response.body,{status:response.status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
  },
};
