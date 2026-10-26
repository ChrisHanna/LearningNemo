"""Read retained launch diagnostics for one exact run, without replaying it."""

import argparse
import json
import re
import subprocess

from task_agent.console.invoice_remote_runtime import HOST_PREFIX


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-f0-9]{32}',args.run_id):
        raise ValueError('Exact run ID required')
    script = HOST_PREFIX + f'''
import re,stat
directory=pathlib.Path('/var/lib/learningnemo-invoice-cache/runs')/{args.run_id!r}
assert not directory.is_symlink()
def read(name):
    path=directory/name
    if not path.exists():return None
    assert not path.is_symlink() and stat.S_ISREG(path.stat().st_mode)
    with path.open('rb') as source:
        source.seek(max(0,path.stat().st_size-2400))
        return source.read(2400).decode(errors='replace')
def redact(text):
    text=re.sub(r'eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+','<jwt-redacted>',text or '')
    text=re.sub(r'(?i)Bearer\\s+\\S+','Bearer <redacted>',text)
    return '\\n'.join(line for line in text.splitlines() if not re.search(r'(?i)password|access_token|client_secret|capability[=:]',line))
events=read('events.jsonl')
journal=subprocess.run(['journalctl','--unit','invoice-agent-'+{args.run_id!r},'--no-pager','--lines=8','--output=cat'],capture_output=True,text=True,timeout=20)
gateway=subprocess.run(['runuser','-u','sawadmin','--','env',*[key+'='+value for key,value in environment.items()],'journalctl','--user','--unit=openshell-gateway.service','--no-pager','--lines=1000','--output=cat'],capture_output=True,text=True,timeout=20)
needles=({args.run_id!r},'ip-'+{args.run_id!r}[:16],'unauthorized','no space left','failed to pull')
gateway_lines=[line for line in gateway.stdout.splitlines() if any(needle in line.lower() for needle in needles)]
result={{'run_id':{args.run_id!r},'files':sorted(path.name for path in directory.iterdir()),
    'exit':read('exit.json'),'stderr':redact(read('error.log')),
    'events_bytes':(directory/'events.jsonl').stat().st_size if events is not None else None,
    'worker_journal':redact(journal.stdout)[-700:],'gateway_journal':redact('\\n'.join(gateway_lines))[-1700:]}}
print('INVOICE_LAUNCH_DIAGNOSTIC '+json.dumps(result,separators=(',',':')))
PY
'''
    result = json.loads(subprocess.check_output(['az','vm','run-command','invoke','-g','rg-learningnemo-saw-dev',
        '-n','vm-learningnemo-saw-dev','--command-id','RunShellScript','--scripts',script,'-o','json'],text=True,timeout=180))
    output = '\n'.join(item.get('message','') for item in result.get('value',[]))
    print(output)


if __name__=='__main__':
    main()