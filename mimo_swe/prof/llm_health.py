"""LLM and tool-call latency for recent profiling trials, from mini-swe-agent per-message timestamps. usage: llm_health.py <since_epoch>"""
import json,glob,os,re,sys,statistics as S,collections
since=float(sys.argv[1])
llm=[];tool=[];exits=collections.Counter();excs=collections.Counter();errlines=collections.Counter();n=0;slow=[]
for d in glob.glob('jobs/prof-r1/*/'):
    tj=d+'agent/mini-swe-agent.trajectory.json'
    if not os.path.exists(tj) or os.path.getmtime(d+'config.json')<since: continue
    n+=1
    t=json.load(open(tj)); m=t['messages']
    exits[t.get('info',{}).get('exit_status')]+=1
    prev_tool=None
    for x in m:
        ts=(x.get('extra') or {}).get('timestamp')
        if ts is None: continue
        if x['role']=='assistant':
            if prev_tool: llm.append(ts-prev_tool); 
            if prev_tool and ts-prev_tool>300: slow.append((d,ts-prev_tool))
            last_a=ts
        elif x['role']=='tool':
            tool.append(ts-last_a); prev_tool=ts
    txt=open(d+'agent/mini-swe-agent.txt',errors='ignore').read() if os.path.exists(d+'agent/mini-swe-agent.txt') else ''
    for pat in ['APIConnectionError','APIError','Timeout','RateLimit','InternalServerError','502','503','504','Retrying','ServiceUnavailable','BadRequest','ContextWindow']:
        c=len(re.findall(pat,txt))
        if c: errlines[pat]+=c
    if os.path.exists(d+'result.json'):
        e=(json.load(open(d+'result.json')).get('exception_info') or {}).get('exception_type')
        if e: excs[e]+=1
def q(a,p): a=sorted(a); return round(a[int(p*(len(a)-1))],2) if a else None
print('trials',n,'exit_status',dict(exits)); print('exceptions',dict(excs)); print('error mentions in agent logs',dict(errlines))
print('LLM call s: n',len(llm),'mean',round(S.mean(llm),2),'p50',q(llm,.5),'p90',q(llm,.9),'p99',q(llm,.99),'max',q(llm,1))
print('tool call s: n',len(tool),'mean',round(S.mean(tool),2),'p50',q(tool,.5),'p90',q(tool,.9),'p99',q(tool,.99),'max',q(tool,1))
print('LLM calls >300s',len(slow),slow[:3])
