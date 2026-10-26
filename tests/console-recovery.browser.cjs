// Real browser/UI interaction with explicitly synthetic run-status responses.
// No investigation is created, cancelled, deleted or replayed by this check.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({channel:'msedge',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    const errors = [], mutations = [];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{
      if(request.url().includes('/api/invoices/') && !['GET','HEAD','OPTIONS'].includes(request.method()))mutations.push(request.method()+' '+request.url());
    });
    if(process.argv.includes('--local-asset'))await page.route('**/assets/invoice-workflow.js',route=>route.fulfill({
      contentType:'application/javascript',body:fs.readFileSync(path.join(__dirname,'../src/task_agent/console/static/invoice-workflow.js'),'utf8'),
    }));
    const identifier = require('node:crypto').randomUUID().replaceAll('-','');
    let serverState = 'running';
    await page.route(new RegExp('/api/invoices/jobs/'+identifier+'(?:/events)?(?:\\?.*)?$'),route=>{
      const events=route.request().url().includes('/events');
      return route.fulfill({contentType:'application/json',body:JSON.stringify(events
        ? {source:'synthetic-ui-test',job_id:identifier,events:[]}
        : {source:'synthetic-ui-test',job_id:identifier,kind:'planning',target_id:'d'.repeat(32),state:serverState})});
    });
    await page.goto('https://learningnemo.ai/',{waitUntil:'domcontentloaded'});
    await page.locator('#loginOperatorButton').click();
    await page.waitForFunction(()=>state.session?.status==='authenticated' && state.session?.persona==='operator');
    const key = await page.evaluate(()=>state.session.persona+':'+(state.session.storageKey || state.session.accountFingerprint));
    await page.evaluate(({key,identifier})=>sessionStorage.setItem('invoice-job:'+key,JSON.stringify({job_id:identifier})),{key,identifier});
    await page.reload({waitUntil:'domcontentloaded'});
    const recovery=page.getByRole('region',{name:'Workspace recovery'});
    // The semantic section is labelled; locate by aria-label even on browsers
    // that do not map a section to region until it has an accessible name.
    const panel=page.locator('[aria-label="Workspace recovery"]');
    await panel.waitFor({state:'visible',timeout:45000});
    await page.waitForFunction(()=>!document.querySelector('[aria-label="Workspace recovery"] button')?.disabled);
    page.once('dialog',dialog=>dialog.accept());
    await panel.getByRole('button',{name:'Reset view',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[aria-label="Workspace recovery"]')?.textContent.includes('View not reset.'));
    assert.ok(await page.evaluate(key=>sessionStorage.getItem('invoice-job:'+key)!==null,key));
    serverState='uncertain';
    page.once('dialog',dialog=>dialog.accept());
    await panel.getByRole('button',{name:'Reset view',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[aria-label="Workspace recovery"]')?.textContent.includes('View reset.'));
    assert.equal(await page.evaluate(key=>sessionStorage.getItem('invoice-job:'+key),key),null);
    const retained=await page.evaluate(key=>JSON.parse(sessionStorage.getItem('invoice-recovery:'+key)),key);
    assert.ok(retained.some(item=>item.job_id===identifier));
    await panel.locator('summary').click();
    assert.ok((await panel.innerText()).includes(identifier));
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
    if(process.env.RECOVERY_SCREENSHOT)await page.screenshot({path:process.env.RECOVERY_SCREENSHOT,fullPage:true});
    assert.deepEqual(mutations,[]);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({evidence_mode:'synthetic-status-real-browser',asset:process.argv.includes('--local-asset')?'workspace-override':'deployed',
      active_run_reset:'blocked',terminal_reset:'passed',request_id_retained:true,invoice_mutations:mutations,page_errors:errors,mobile_overflow:false}));
  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});