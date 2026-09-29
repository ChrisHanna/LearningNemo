(() => {
  const view = window.invoiceView;
  const node = (tag,text='',className='',scrollKey='') => { const value=document.createElement(tag);value.textContent=text;value.className=className;if(scrollKey)value.setAttribute('data-scroll-key',scrollKey);return value; };
  const amount = value => Number.isSafeInteger(value) ? (value/100).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}) : 'Not observed';
  window.renderInvoiceSandboxInventory = function ({ inventory, loading, error, permitted, busy, pending, refresh, remove }) {
    const section=node('details','','invoice-sandbox-inventory','sandbox-inventory');section.setAttribute('aria-label','Sandbox inventory');
    section.append(node('summary',Number.isInteger(inventory?.remaining_count)?'Sandboxes: '+inventory.remaining_count+' retained / '+inventory.limit+' limit':'Sandboxes: count not checked'));
    const reload=node('button',loading?'Reading sandboxes':'Refresh sandboxes','button button-secondary');reload.type='button';reload.prepend(createIcon('refresh-cw'));reload.disabled=loading||busy;reload.addEventListener('click',refresh);section.append(reload);
    if(error){const status=node('p',error,'mission-action-status');status.setAttribute('role','status');section.append(status);}
    if(!inventory)return section;
    section.append(node('p','Automatic cleanup at '+inventory.cleanup_at_count+' / target '+inventory.target_count+' / '+inventory.free_slots+' free slots / '+inventory.free_gib+' GiB free','authority-note'));
    if(inventory.checked_at)section.append(node('time','Inventory observed '+new Date(inventory.checked_at).toUTCString()));
    const wrap=node('div','','ledger-scroll','sandbox-inventory-rows'),table=node('table'),head=node('thead'),heading=node('tr');
    for(const title of ['Sandbox','State','Deletion eligibility','Action'])heading.append(node('th',title));head.append(heading);table.append(head);
    const body=node('tbody');
    for(const item of inventory.sandboxes||[]){
      const row=node('tr'),identity=node('td');identity.append(node('strong',item.name),node('code',item.sandbox_id));
      const action=node('td'),button=node('button','','button button-secondary');button.type='button';button.prepend(createIcon('trash-2'));
      const waiting=pending.has(item.sandbox_id);const title=waiting?'Deletion unconfirmed; refresh inventory':item.deletable?'Delete sandbox '+item.name:'Protected: '+item.reason;
      button.title=title;button.setAttribute('aria-label',title);button.disabled=busy||!permitted||!item.deletable||waiting;button.addEventListener('click',()=>remove(item.sandbox_id));action.append(button);
      row.append(identity,node('td',item.phase),node('td',waiting?'Deletion requested; do not repeat':item.reason),action);body.append(row);
    }
    table.append(body);wrap.append(table);section.append(wrap);return section;
  };
  window.renderInvoiceEvidence = function (row, observation, before) {
    const section=node('section','','invoice-ledger');section.setAttribute('aria-label','Observed invoice evidence');
    section.append(node('h3','Invoice records from this reading'));
    if (!view.latestEvidence(row,observation)) { section.append(node('p','No matching database observation.'));return section; }
    const summary=observation.summary;
    const model=view.businessOutcome(row,before,observation);
    const figures=node('dl','','invoice-comparison');
    for (const metric of model.metrics) {
      const format=value=>metric.money?amount(value):value??'Not observed';
      const entry=node('div');entry.append(node('dt',metric.label),node('dd',model.compare?'Planning baseline: '+format(metric.before)+' / Latest database read: '+format(metric.after):format(metric.after)));figures.append(entry);
    }
    section.append(figures,node('p','Recorded '+new Date(observation.observed_at).toLocaleString(undefined,{timeZone:'UTC',timeZoneName:'short'})+' / database revision '+summary.revision,'authority-note'));
    const rows=observation.rows||[], orderLabels=new Map(), counts=new Map();
    for(const item of rows) { if(!orderLabels.has(item.order_id))orderLabels.set(item.order_id,'Order '+(orderLabels.size+1));if(!item.quarantined)counts.set(item.order_id,(counts.get(item.order_id)||0)+1); }
    const wrap=node('div','','ledger-scroll','ledger:'+row.plan_json.plan_id),table=node('table'),head=node('thead'),heading=node('tr');
    for(const name of ['Order key','Import attempt','Amount','Record state'])heading.append(node('th',name));head.append(heading);table.append(head);
    const body=node('tbody');
    for(const item of rows.slice(0,48)) { const entry=node('tr');entry.className=item.quarantined?'quarantined':counts.get(item.order_id)>1?'duplicate':'';entry.append(node('td',orderLabels.get(item.order_id)),node('td',String(item.attempt)),node('td',amount(item.amount_cents)),node('td',item.quarantined?'Quarantined':counts.get(item.order_id)>1?'Repeated order key':'Active'));body.append(entry); }
    table.append(body);wrap.append(table);section.append(wrap,node('p','Equal amounts alone do not make an invoice a duplicate. Order keys distinguish legitimate purchases.','authority-note'));
    const receipts=observation.receipts||[];
    if(receipts.length){const list=node('ol','','sql-receipt-list');for(const receipt of receipts){list.append(node('li','Step '+receipt.step_id+' / '+invoiceView.operation(receipt.operation).title+' / revision '+receipt.revision));}section.append(node('h4','Persisted SQL receipts'),list);}
    const auth=observation.authorization;
    if(auth)section.append(node('p',auth.identity+' identity → '+auth.persona+' role → '+auth.required_scopes.join(' + ')+' → '+auth.ownership,'request-authorization'));
    return section;
  };
  window.renderInvoiceSandboxTest = function ({ job, events, pending = false, permitted = false, busy = false, request, reconnect, readOnly = false }) {
    const model = view.sandboxTest(job, events, pending), section = node('section','','same-sandbox-test');
    section.setAttribute('aria-label','Selected agent sandbox test');
    section.append(node('h2','This agent\'s sandbox'),node('strong',model.title,model.confirmed?'confirmed':''));
    const agentRuntime = job?.agent_runtime;
    if (agentRuntime?.runtime === 'agents_api') {
      const context = agentRuntime.user_context || {};
      const identity = node('dl', '', 'sandbox-test-binding');
      for (const [label, value] of [
        ['Runtime', 'OpenAI Agents API'], ['Session', agentRuntime.session_id || 'Creation unconfirmed'],
        ['Environment', agentRuntime.environment_id || 'Not connected'], ['API lifecycle', agentRuntime.state],
        ['Human sponsor', context.sponsor_hash], ['Persona', context.persona],
        ['Effective permissions', (context.permissions || []).join(' · ')], ['Authority expires', context.expires_at],
      ]) identity.append(node('dt', label), node('dd', value || 'Not observed'));
      section.append(identity, node('p', 'Verified user context accompanies this run. The trusted invoice gateway enforces its capability; API completion alone does not establish a database repair.', 'authority-note'));
    }
    if (model.runId) {
      const binding=node('dl','','sandbox-test-binding');
      for(const [label,value] of [['Agent',job.kind==='execution'?'Execution':'Planning'],['Run ID',model.runId],['Sandbox ID',model.sandboxId||'Not yet observed']])binding.append(node('dt',label),node('dd',value));
      section.append(binding,node('p','Forbidden request: '+(job.kind==='planning'?'Request an invoice repair':'Read invoice diagnostics'), 'authority-note'));
    }
    if (model.available && !readOnly) {
      const action=node('button','Test this sandbox','button button-primary');action.type='button';action.prepend(createIcon('shield-check'));action.disabled=true;
      action.disabled=busy||!permitted;
      action.addEventListener('click',()=>request(model.runId,model.sandboxId));section.append(action);
    }
    if (model.requested && !model.proof) section.append(node('p','The controlled probe runs after capability revocation and before normal cleanup.','authority-note'));
    if (model.proof) {
      section.append(node('p','Controlled probe in the same sandbox, not a model request. No credential was passed to the probe.','authority-note'));
      const responses=node('dl','','challenge-decisions');
      for(const response of model.proof.requests||[]) responses.append(node('dt',(response.tool===model.control?'Control: ':'Forbidden: ')+(view.tools[response.tool]||response.tool)),node('dd',response.status?'HTTP '+response.status:'No HTTP response'));
      section.append(responses,node('p',model.proof.sandbox_stopped?'Sandbox stop confirmed':'Sandbox stop unconfirmed'));
      const raw=node('details','','','bound-probe:'+model.runId);raw.append(node('summary','Same-sandbox enforcement receipt'),node('pre',JSON.stringify(model.proof,null,2),'','bound-probe-json:'+model.runId));section.append(raw);
    }
    if (model.runId && reconnect && !readOnly) {
      const refresh=node('button','Refresh this run','button button-secondary');refresh.type='button';refresh.prepend(createIcon('refresh-cw'));refresh.disabled=busy;refresh.addEventListener('click',reconnect);section.append(refresh);
    }
    return section;
  };
  window.renderInvoiceChallenge = function ({ result, kind, onKind, permitted, blocked, busy, start, reconnect, executionMode = 'openshell' }) {
    const scenarios=invoiceView.investigations,selected=scenarios[kind]?kind:'query-draft';
    const live=executionMode==='openshell';
    const section=node('section','','invoice-challenge invoice-investigation-launch');section.setAttribute('aria-label','Test OpenShell sandbox security');
    const proof=invoiceView.challengeProof(result);
    const intro=node('header','','investigation-intro');
    const heading=node('div');heading.append(node('span',live?'OPEN SHELL SANDBOX TESTS':'LOCAL WALKTHROUGH','eyebrow'),node('h2','Test OpenShell security boundaries'),node('p',live?'Choose a focused scenario to see what a fresh non-root MicroVM permits or blocks under the checked-in Planning policy, then inspect its enforcement evidence.':'Explore the sandbox test flow with deterministic receipts. This preview does not create a MicroVM or enforce OpenShell policy.'));
    const environment=node('div','','investigation-environment '+(live?'live':'fixture'));environment.append(createIcon(live?'shield-check':'monitor'),node('small','Execution target'),node('strong',live?'OpenShell MicroVM':'Fixture preview'),node('span',live?'Planning policy · UID 998 · retained after stop':'No sandbox · no model · no cloud access'));
    intro.append(heading,environment);section.append(intro,node('h3','1. Choose a test scenario','investigation-step-title'));
    const options=node('div','','investigation-options');options.setAttribute('role','group');options.setAttribute('aria-label','Investigation scenario');
    for(const [value,metadata] of Object.entries(scenarios)){
      const option=node('button','','investigation-option');option.type='button';option.setAttribute('aria-pressed',String(value===selected));option.disabled=busy||blocked;
      const copy=node('span');copy.append(node('strong',metadata.title),node('small',metadata.summary));option.append(createIcon(metadata.icon),copy);
      option.addEventListener('click',()=>onKind(value));options.append(option);
    }
    section.append(options,node('h3','2. Review the expected boundary','investigation-step-title'));
    const metadata=scenarios[selected];
    const preview=node('ol','','challenge-proof-chain');
    for(const [label,text,icon] of [['Attempt',metadata.attempt,metadata.icon],['Boundary',metadata.boundary,'shield-check'],['Expected',metadata.expected,'lock-keyhole']]){const item=node('li');item.append(createIcon(icon),node('small',label),node('strong',text));preview.append(item);}section.append(preview);
    const policy=node('div','','investigation-policy');
    const policyHeading=node('div','','investigation-policy-heading');policyHeading.append(node('span',live?'ENFORCED POLICY':'POLICY PREVIEW','eyebrow'),node('h4','Planning sandbox policy'),node('code','infra/next-phase/openshell/invoice-planning-policy.yaml'));
    const policyCode=node('pre','','investigation-policy-code','investigation-policy-code');policyCode.append(node('code',`filesystem_policy:
  include_workdir: false
  read_only:
    - /app
  read_write:
    - /tmp
network_policies:
  invoice_planning:
    endpoints:
      - host: ca-nemo-invoice-planning-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io
        port: 443`));
    policy.append(policyHeading,policyCode,node('p',live?'OpenShell enforces this checked-in policy for the launched MicroVM.':'This is the policy the deployed OpenShell flow enforces; the local walkthrough only previews it.'));section.append(policy);
    const launch=node('div','','investigation-action');const launchCopy=node('div');launchCopy.append(node('h3','3. '+(live?'Run the OpenShell test':'Preview the test')),node('p',live?'The request creates one isolated sandbox and records its policy, identity, attempted action, cleanup, and enforcement evidence.':'The preview returns the expected receipt shape so the interface can be reviewed without cloud authority.'));
    const button=node('button',live?'Run OpenShell test':'Preview test','button button-primary');button.type='button';button.prepend(createIcon(live?'play':'scan-search'));button.disabled=busy||blocked||!permitted;button.addEventListener('click',()=>start(selected));launch.append(launchCopy,button);section.append(launch);
    if(blocked)section.append(node('p','Another sandbox action is active or unconfirmed. Reconnect before starting a new investigation.','authority-note'));
    if(result){
      const resultHeading=node('div','','investigation-result-heading');resultHeading.append(node('span','RESULT','eyebrow'),node('h3',result.state==='finished'?'OpenShell test receipt':'OpenShell test status'));section.append(resultHeading);
      const refresh=node('button','Refresh result','button button-secondary');refresh.type='button';refresh.prepend(createIcon('refresh-cw'));refresh.addEventListener('click',reconnect);refresh.disabled=busy;section.append(refresh);
      const record=result.result,resultMetadata=scenarios[result.kind],successful=proof.successful??proof.confirmed;
      section.append(node('small',(resultMetadata?.title||'Legacy route challenge')+' / '+(record?.evidence_mode==='fixture'?'local fixture':'recorded result'),'authority-note'));
      section.append(node('strong',proof.title,'challenge-verdict '+(successful?'confirmed':'')));
      if(record){
        const execution=node('section','','sandbox-execution-proof');execution.setAttribute('aria-label','Sandbox execution proof');
        execution.append(node('h4',record.evidence_mode==='fixture'?'Execution preview':'Verified sandbox execution'));
        const executionFacts=node('dl','','challenge-decisions');
        const facts=record.evidence_mode==='fixture'?
          [['Mode','Deterministic fixture'],['Sandbox created',record.sandbox_created===false?'No':'Unconfirmed']]:
          [['Execution',record.executed_in_sandbox===true?'Confirmed inside sandbox':'Unconfirmed'],['Runtime',record.sandbox_runtime],['Executor',record.sandbox_executor],['Run ID',record.run_id],['Sandbox ID',record.sandbox_id],['Process identity',Number.isInteger(record.uid)?'UID '+record.uid:null],['Policy hash',record.policy_hash],['Final state',record.sandbox_stopped===true&&record.sandbox_retained===true?'Stopped and retained':'Unconfirmed']];
        for(const [label,value] of facts)if(value)executionFacts.append(node('dt',label),node('dd',value));execution.append(executionFacts);section.append(execution);
      }
      const eventTitles={
        'preparing-sandbox':'Preparing OpenShell sandbox','sandbox-bound':'Sandbox and policy bound',
        'investigation-started':'Executing investigation in sandbox','investigation-recorded':'Sandbox receipt captured',
        'probe-started':'Executing route probe in sandbox','probe-recorded':'Sandbox probe receipt captured',
        'sandbox-stopped':'Sandbox stopped and retained','probe-progress':'Sandbox progress',
      };
      const timeline=node('ol','','sandbox-execution-timeline');timeline.setAttribute('aria-label','Sandbox execution lifecycle');
      for(const event of result.events||[]){const item=node('li');item.append(node('strong',eventTitles[event.event_type]||'Sandbox event'),node('span',event.reason||event.label||'Recorded'));if(event.observed_at)item.append(node('time',new Date(event.observed_at).toLocaleTimeString()));timeline.append(item);}section.append(timeline);
      if(resultMetadata&&record){const facts=node('dl','','challenge-decisions');for(const [label,value] of [['Operation',record.operation],['Outcome',record.outcome],['Path',record.path],['Target',record.target],['Link created',record.link_created===true?'Yes':null],['Link removed',record.link_removed===true?'Yes':null],['Destination',record.destination&&record.destination+':'+record.destination_port],['Method',record.method],['Route',record.route],['HTTP status',Number.isInteger(record.http_status)?String(record.http_status):null],['Query class',record.query_class],['Query executed',record.query_executed===false?'No':null],['Bytes observed',Number.isInteger(record.bytes)?String(record.bytes):null]])if(value)facts.append(node('dt',label),node('dd',value));section.append(facts);}
      else if(record?.requests){const list=node('dl','','challenge-decisions');for(const request of record.requests)list.append(node('dt',(request.tool===proof.control?'Control / ':'Forbidden / ')+(invoiceView.tools[request.tool]||request.tool)),node('dd',request.status?'HTTP '+request.status:'No HTTP response'));section.append(list);}
      if(record)section.append(node('p',record.evidence_mode==='fixture'?'Fixture cleanup state recorded; no sandbox existed.':record.sandbox_stopped===true?'Sandbox stop confirmed; retained':'Sandbox stop not confirmed','authority-note'));
      if(record)section.append(node('p',proof.confirmed?'Live sandbox behavior matched the checked-in policy and bound enforcement evidence.':proof.fixture?'Local fixture demonstrates the expected contract; no live OpenShell enforcement is claimed.':'No security success is inferred from a timeout, DNS failure, or missing enforcement evidence.','authority-note'));
      if(record?.denial_evidence?.length){const details=node('details','','','probe-receipt:'+result.challenge_id);details.append(node('summary','OpenShell enforcement receipt'),node('pre',record.denial_evidence.join('\n'),'','probe-log:'+result.challenge_id));section.append(details);}
      if(record){const raw=node('details','','','investigation-receipt:'+result.challenge_id);raw.append(node('summary','Bound investigation receipt'),node('pre',JSON.stringify(record,null,2),'','investigation-json:'+result.challenge_id));section.append(raw);}
    }
    return section;
  };
})();