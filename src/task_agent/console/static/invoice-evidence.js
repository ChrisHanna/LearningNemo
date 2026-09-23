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
  window.renderInvoiceChallenge = function ({ result, kind, onKind, permitted, blocked, busy, start, reconnect }) {
    const section=node('section','','invoice-challenge');section.setAttribute('aria-label','Live boundary challenge');
    const proof=invoiceView.challengeProof(result);
    const title=node('div');title.append(node('h2','Separate policy test'),node('p','New isolated sandbox / not the agent\'s sandbox','authority-note'));section.append(title);
    const select=node('select');select.setAttribute('aria-label','Live challenge');
    for(const [kind,text] of [['planning','Planning: attempt a repair route'],['execution','Execution: attempt a diagnostic route']]){const option=node('option',text);option.value=kind;select.append(option);}select.disabled=busy||blocked;section.append(select);
    select.value = kind === 'execution' ? 'execution' : 'planning';
    select.addEventListener('change',()=>onKind(select.value));
    const preview=node('ol','','challenge-proof-chain');
    for(const [label,text,icon] of [['Attempt',kind==='execution'?'Read invoice diagnostics':'Request an invoice repair','arrow-right'],['Boundary','OpenShell route policy','shield-check'],['Expected','Control reaches gateway; forbidden route denied','lock-keyhole']]){const item=node('li');item.append(createIcon(icon),node('small',label),node('strong',text));preview.append(item);}section.append(preview);
    const button=node('button','Run boundary challenge','button button-secondary');button.type='button';button.prepend(createIcon('shield-check'));button.disabled=busy||blocked||!permitted;button.addEventListener('click',()=>start(select.value));section.append(button);
    if(blocked)section.append(node('p','A run or challenge is active or unconfirmed. Reconnect before starting another.','authority-note'));
    if(result){
      const refresh=node('button','Reconnect challenge','button button-secondary');refresh.type='button';refresh.prepend(createIcon('refresh-cw'));refresh.addEventListener('click',reconnect);refresh.disabled=busy;section.append(refresh);
      const record=result.result,confirmed=proof.confirmed;
      section.append(node('small',(result.kind==='execution'?'Execution':result.kind==='planning'?'Planning':'Unconfirmed')+' challenge / recorded result','authority-note'));
      section.append(node('strong',proof.title,'challenge-verdict '+(confirmed?'confirmed':'')));
      const timeline=node('ol');for(const event of result.events||[])timeline.append(node('li',event.reason||event.label));section.append(timeline);
      if(record?.requests){const list=node('dl','','challenge-decisions');for(const request of record.requests)list.append(node('dt',(request.tool===proof.control?'Control / ':'Forbidden / ')+(invoiceView.tools[request.tool]||request.tool)),node('dd',request.status?'HTTP '+request.status:'No HTTP response'));section.append(list);}
      if(record)section.append(node('p',record.sandbox_stopped===true?'Sandbox stop confirmed; retained':'Sandbox stop not confirmed','authority-note'));
      if(record)section.append(node('p',confirmed?'Control reached the gateway (401). Forbidden route was rejected (403), with a matching OpenShell record. No database credential was issued.':'No security success is inferred from a timeout or missing evidence.','authority-note'));
      if(record?.denial_evidence?.length){const details=node('details','','','probe-receipt:'+result.challenge_id);details.append(node('summary','OpenShell enforcement receipt'),node('pre',record.denial_evidence.join('\n'),'','probe-log:'+result.challenge_id));section.append(details);}
    }
    return section;
  };
})();