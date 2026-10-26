"""One-shot public Planning acceptance check; never replays a submitted action."""

import argparse
import json
import os
from pathlib import Path
import uuid

import requests

BASE = 'https://learningnemo.ai'
STATE = Path.home()/'.local/state/learningnemo/live-planning-contract-check.json'


def save(value):
    STATE.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    descriptor = os.open(STATE,os.O_CREAT|os.O_TRUNC|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w') as output:
        os.fchmod(output.fileno(),0o600)
        json.dump(value,output,indent=2)
        output.flush()
        os.fsync(output.fileno())


def main():
    global STATE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('start','status'))
    parser.add_argument('--state',type=Path,default=STATE)
    args = parser.parse_args()
    STATE = args.state
    session = requests.Session()
    session.headers['Origin'] = BASE
    if args.action=='start':
        if STATE.exists():
            raise RuntimeError('Existing acceptance request retained; use status, not replay')
        response = session.get(BASE+'/api/bootstrap',timeout=45)
        response.raise_for_status()
        csrf = response.json()['csrfToken']
        session.headers['X-LearningNeMo-CSRF'] = csrf
        response = session.post(BASE+'/api/auth/start',json={'persona':'operator'},timeout=45)
        response.raise_for_status()
        state = {'scenario_id':uuid.uuid4().hex,'job_id':uuid.uuid4().hex,'cookies':session.cookies.get_dict(),
                 'csrf':csrf,'scenario_submitted':False,'job_submitted':False,'after':0,'events':[]}
        save(state)
        state['scenario_submitted'] = True
        save(state)
        response = session.post(BASE+'/api/invoices/scenarios',json={'scenario_id':state['scenario_id'],'variant':'lost-acknowledgement'},timeout=175)
        print('SCENARIO',response.status_code,flush=True)
        response.raise_for_status()
        state['job_submitted'] = True
        save(state)
        response = session.post(BASE+'/api/invoices/jobs',json={'job_id':state['job_id'],'kind':'planning','target_id':state['scenario_id']},timeout=45)
        print('PLANNING_ADMISSION',response.status_code,flush=True)
        response.raise_for_status()
        state['cookies'] = session.cookies.get_dict()
        save(state)
        print(json.dumps({'scenario_id':state['scenario_id'],'job_id':state['job_id'],'replayed':False}))
        return
    state = json.loads(STATE.read_text())
    session.cookies.update(state['cookies'])
    session.headers['X-LearningNeMo-CSRF'] = state['csrf']
    response = session.get(BASE+'/api/invoices/jobs/'+state['job_id'],timeout=90)
    print('STATUS_HTTP',response.status_code,flush=True)
    response.raise_for_status()
    job = response.json()
    response = session.get(BASE+'/api/invoices/jobs/'+state['job_id']+'/events',params={'after':state['after']},timeout=90)
    response.raise_for_status()
    events = response.json().get('events',[])
    state['events'].extend(events)
    if events:
        state['after'] = max(event['sequence'] for event in state['events'])
    state['last_status'] = job
    save(state)
    result = job.get('result') or {}
    print(json.dumps({'job_id':state['job_id'],'state':job.get('state'),'outcome':result.get('outcome'),
                     'detail':result.get('detail'),'plan_hash':result.get('plan_hash'),
                     'events':[{'type':event.get('event_type'),'source':event.get('source'),'sandbox_id':event.get('sandbox_id')} for event in state['events']]},indent=2))


if __name__=='__main__':
    main()