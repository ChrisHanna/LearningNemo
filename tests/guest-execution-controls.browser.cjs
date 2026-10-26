// Synthetic plan rendering + live denial of an unowned execution request.
// Does not create a scenario, consume a launch, or start an agent.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:900}});
    const planId='f'.repeat(32),scenarioId='e'.repeat(32);
    let planState='approved';
    const checks={no_duplicates:true,legitimate_invoices_preserved:true,total_reconciles:true,idempotent_version_active:true,replay_created_no_invoices:true};
    await page.route('**/api/invoices/plans',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({source:'azure-sql',plans:[{
      plan_hash:'d'.repeat(64),state:planState,approval_expires_at:new Date(Date.now()+1800000).toISOString(),
      execution_before:new Date(Date.now()+900000).toISOString(),scenario_expires_at:new Date(Date.now()+3600000).toISOString(),
      verification_json:planState==='verified'?checks:null,
      plan_json:{plan_id:planId,scenario_id:scenarioId,sponsor_hash:'a'.repeat(64),planning_run_id:'b'.repeat(32),planning_sandbox_id:'c'.repeat(32),
        evidence_hash:'0'.repeat(64),created_at:new Date().toISOString(),diagnosis:'Synthetic UI fixture / no business data',rationale:'Bound UI verification only',risks:[],
        steps:[{step_id:1,operation:'invoice.rebuild-total.v1',target:scenarioId,expected_revision:1,duplicate_set_hash:null}]}
    }]})}));
    await page.goto('https://learningnemo.ai/',{waitUntil:'domcontentloaded'});
    await page.locator('#loginOperatorButton').click();
    await page.waitForFunction(()=>state.session?.status==='authenticated' && state.session.persona==='operator');
    await page.locator('option[value="'+planId+'"]').waitFor({state:'attached',timeout:60000});
    await page.getByRole('combobox',{name:'Investigation',exact:true}).selectOption(planId);
    await page.getByRole('button',{name:'Start Execution agent',exact:true}).waitFor({state:'visible'});
    assert.equal(await page.getByRole('button',{name:'Start Execution agent',exact:true}).isEnabled(),true);
    const denied=await page.evaluate(async()=>{
      try{await api('/api/invoices/jobs',{method:'POST',body:JSON.stringify({job_id:crypto.randomUUID().replaceAll('-',''),kind:'execution',target_id:'f'.repeat(32),plan_hash:'d'.repeat(64)})});return {status:202};}
      catch(error){return {status:error.status};}
    });
    assert.equal(denied.status,409,'Live backend must reject the synthetic/unowned plan before quota or work');
    planState='verified';
    await page.getByRole('button',{name:'Refresh investigations',exact:true}).click();
    await page.locator('#invoice-tab-4').click();
    await page.getByRole('button',{name:'Acknowledge completion',exact:true}).waitFor({state:'visible'});
    console.log(JSON.stringify({evidence_mode:'synthetic-plan-real-browser-live-authorization',guest_execution_control:'enabled',completion_control:'visible',unowned_execution_http:denied.status,agents_started:0}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});