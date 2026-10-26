// Live, side-effectful acceptance limited to a new disposable invoice scenario.
// Two distinct guest browser identities; no mocked API responses or action replay.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');

(async()=>{
  const evidence=process.env.GUEST_CYCLE_EVIDENCE;
  assert.ok(evidence,'GUEST_CYCLE_EVIDENCE must name a new private evidence file');
  assert.equal(fs.existsSync(evidence),false,'Existing attempt retained; never replay this acceptance');
  fs.mkdirSync(path.dirname(evidence),{recursive:true});
  const record={evidence_mode:'live',started_at:new Date().toISOString(),actions:[],job_ids:[]};
  const save=()=>fs.writeFileSync(evidence,JSON.stringify(record,null,2),{mode:0o600});
  fs.closeSync(fs.openSync(evidence,'wx',0o600));save();
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const contexts=[];
  const pages=[];
  const read=async(page,route)=>page.evaluate(route=>api(route),route);
  const intent=(action)=>{record.actions.push({action,requested_at:new Date().toISOString()});save();};
  async function actor(persona){
    const context=await browser.newContext({viewport:{width:1280,height:900}});contexts.push(context);
    const page=await context.newPage();pages.push(page);page.setDefaultTimeout(180000);
    page.on('request',request=>{
      if(request.method()!=='POST')return;
      if(request.url().endsWith('/api/invoices/scenarios')){
        record.scenario_id=request.postDataJSON().scenario_id;save();
      }
      if(request.url().endsWith('/api/invoices/jobs')){
        const body=request.postDataJSON();
        if(!record.job_ids.includes(body.job_id))record.job_ids.push(body.job_id);
        record.last_job_request=body;save();
      }
    });
    page.on('response',async response=>{
      try{
        const match=response.url().match(/\/api\/invoices\/jobs\/([a-f0-9]{32})$/);
        if(!match || response.request().method()!=='GET' || !record.job_ids.includes(match[1]))return;
        const job=await response.json();
        record.observed_jobs ||= {};
        record.observed_jobs[match[1]]={http:response.status(),state:job.state,outcome:job.result?.outcome,detail:job.result?.detail,
          plan_id:job.result?.plan?.plan_id,observed_at:new Date().toISOString()};save();
      }catch{}
    });
    await page.goto('https://learningnemo.ai/',{waitUntil:'domcontentloaded'});
    await page.locator(persona==='operator'?'#loginOperatorButton':'#loginApproverButton').click();
    await page.waitForFunction(persona=>state.session?.status==='authenticated' && state.session.persona===persona,persona);
    record['actor_'+contexts.indexOf(context)]=await context.storageState();save();
    return page;
  }
  async function awaitAction(page,label,jobId,timeout=360000){
    await Promise.race([
      page.getByRole('button',{name:label,exact:true}).waitFor({state:'visible',timeout}),
      page.waitForResponse(async response=>response.url().endsWith('/api/invoices/jobs/'+jobId)
        && response.request().method()==='GET' && response.status()===200 && (await response.json()).state==='uncertain',{timeout})
        .then(async response=>{const job=await response.json();throw new Error('Persisted run '+jobId+' is uncertain: '+JSON.stringify(job.result));}),
    ]);
  }
  async function clickJob(page,label){
    intent(label);
    const receipt=page.waitForResponse(response=>response.url().endsWith('/api/invoices/jobs') && response.request().method()==='POST');
    await page.getByRole('button',{name:label,exact:true}).click();
    const response=await receipt,body=await response.json();
    record.last_admission={status:response.status(),...body};save();
    assert.equal(response.status(),202,JSON.stringify(body));
    if(!record.job_ids.includes(body.job_id))record.job_ids.push(body.job_id);save();
    return body.job_id;
  }
  try{
    const operator=await actor('operator');
    intent('Create invoice fixture');
    const created=operator.waitForResponse(response=>response.url().endsWith('/api/invoices/scenarios') && response.request().method()==='POST');
    await operator.getByRole('button',{name:'Create invoice fixture',exact:true}).click();
    const scenarioResponse=await created,scenario=await scenarioResponse.json();
    assert.equal(scenarioResponse.status(),201,JSON.stringify(scenario));
    record.scenario_id=scenario.scenario_id;save();
    const planning=await clickJob(operator,'Analyze invoices');
    console.log('PLANNING_STARTED '+planning);
    await awaitAction(operator,'Submit for approval',planning,720000);
    const queue=await read(operator,'/api/invoices/plans');
    const row=queue.plans.find(row=>row.plan_json.planning_run_id===planning);
    assert.ok(row,'New Planning run must have an owned persisted plan');
    record.plan_id=row.plan_json.plan_id;record.plan_hash=row.plan_hash;save();
    intent('Submit for approval');
    const submitted=operator.waitForResponse(response=>response.url().endsWith('/'+record.plan_id+'/submit') && response.request().method()==='POST');
    await operator.getByRole('button',{name:'Submit for approval',exact:true}).click();
    assert.equal((await submitted).status(),200);
    console.log('PLAN_SUBMITTED '+record.plan_id);
    const approver=await actor('approver');
    const picker=approver.getByRole('combobox',{name:'Investigation',exact:true});
    await approver.locator('option[value="'+record.plan_id+'"]').waitFor({state:'attached'});
    await picker.selectOption(record.plan_id);
    const fingerprints=await Promise.all([operator.evaluate(()=>state.session.accountFingerprint),approver.evaluate(()=>state.session.accountFingerprint)]);
    assert.notEqual(fingerprints[0],fingerprints[1]);
    record.distinct_guest_identities=true;save();
    intent('Approve plan');
    const approved=approver.waitForResponse(response=>response.url().endsWith('/api/invoices/review/'+record.plan_id) && response.request().method()==='POST');
    await approver.getByRole('button',{name:'Approve plan',exact:true}).click();
    assert.equal((await approved).status(),200);
    console.log('GUEST_APPROVAL_RECORDED '+record.plan_id);
    await operator.getByRole('button',{name:'Refresh investigations',exact:true}).click();
    await operator.waitForFunction(()=>!document.querySelector('button[aria-label="Refresh investigations"]')?.disabled);
    await operator.locator('#invoice-tab-3').click();
    const execution=await clickJob(operator,'Start Execution agent');
    console.log('EXECUTION_STARTED '+execution);
    await awaitAction(operator,'Acknowledge completion',execution);
    const finished=await read(operator,'/api/invoices/jobs/'+execution);
    assert.equal(finished.state,'finished',JSON.stringify(finished.result));
    assert.ok(Object.values(finished.result.checks).length===5 && Object.values(finished.result.checks).every(value=>value===true));
    record.execution_checks=finished.result.checks;
    const activity=await read(operator,'/api/invoices/jobs/'+execution+'/events?after=0');
    const events=activity.events;
    assert.ok(events.some(event=>event.event_type==='agent-finished'));
    assert.ok(events.some(event=>event.event_type==='verification-passed'));
    assert.ok(events.some(event=>event.event_type==='sandbox-stopped'));
    record.execution_events=events;save();
    intent('Acknowledge completion');
    const completion=operator.waitForResponse(response=>response.url().endsWith('/'+record.plan_id+'/complete') && response.request().method()==='POST');
    await operator.getByRole('button',{name:'Acknowledge completion',exact:true}).click();
    assert.equal((await completion).status(),200);
    const persisted=await read(operator,'/api/invoices/plans');
    const final=persisted.plans.find(row=>row.plan_json.plan_id===record.plan_id);
    assert.equal(final.state,'completed');assert.equal(final.plan_hash,record.plan_hash);
    const planningActivity=await read(operator,'/api/invoices/jobs/'+planning+'/events?after=0');
    const planningSandbox=planningActivity.events.find(event=>event.event_type==='sandbox-bound')?.sandbox_id;
    const executionSandbox=events.find(event=>event.event_type==='sandbox-bound')?.sandbox_id;
    assert.ok(planningSandbox && executionSandbox && planningSandbox!==executionSandbox);
    record.state='completed';record.planning_sandbox_id=planningSandbox;record.execution_sandbox_id=executionSandbox;
    record.finished_at=new Date().toISOString();save();
    console.log(JSON.stringify({evidence_mode:'live-real-browser',state:record.state,scenario_id:record.scenario_id,
      plan_id:record.plan_id,job_ids:record.job_ids,distinct_guest_identities:true,separate_sandboxes:true,
      verification_checks:record.execution_checks,completion_persisted:true},null,2));
  }catch(error){
    record.state='acceptance-unconfirmed';record.error=error.message;save();
    for(const page of pages){
      try{
        const current=await page.evaluate(()=>({persona:state.session?.persona,text:document.querySelector('#invoiceWorkspace')?.innerText?.slice(-1800)}));
        console.error(JSON.stringify(current));
      }catch{}
    }
    throw error;
  }finally{
    // Persist actor cookies privately for read-only reconciliation if interrupted.
    for(let index=0;index<contexts.length;index++)record['actor_'+index]=await contexts[index].storageState();
    save();
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});