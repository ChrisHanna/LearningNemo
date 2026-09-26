"""NeMo output and execution rails for the invoice agent's trusted model boundary."""

import json
import re

from pydantic import ValidationError

from task_agent.control.invoice_contract import InvoiceStep, PlanningDecision


# Forbidden in model output, model tool-call arguments, and tool results.
SECRET_PATTERNS = (
    r'(?i)\bbearer\s+[a-z0-9._~+/=-]{16,}',
    r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}',
    r'\b[a-f0-9]{32}\.[A-Za-z0-9_-]{40,}',
    r'(?i)\b(account ?key|shared ?access ?key|client[_ ]?secret|password|pwd)\s*[=:]',
    r'(?i)\b(server|data source)\s*=\s*tcp:',
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
    r'(?i)[?&]sig=[a-z0-9%/+=]{16,}',
)

ROLE_TOOLS = {
    'planning': ('invoice_summary', 'invoice_batches', 'publish_decision'),
    'execution': ('execute_step',),
}
TOOL_ARGUMENTS = {'invoice_summary': None, 'invoice_batches': None, 'publish_decision': PlanningDecision, 'execute_step': InvoiceStep}
MAX_TOOL_ARGUMENTS = 16384
MAX_TOOL_RESULT = 65536

OUTPUT_RAIL = 'check invoice output secrets'
TOOL_OUTPUT_RAIL = 'check invoice tool calls'
TOOL_INPUT_RAIL = 'check invoice tool results'
REFUSAL = 'refuse to respond'

COLANG = f'''
define bot refuse to respond
  "The invoice guardrail blocked this content."

define subflow {OUTPUT_RAIL}
  $result = execute detect_regex_pattern(source="output", text=$bot_message)
  if $result["is_match"]
    bot refuse to respond
    stop

define subflow {TOOL_OUTPUT_RAIL}
  $allowed = execute check_invoice_tool_calls(tool_calls=$tool_calls, invoice_kind=$invoice_kind, scenario_id=$scenario_id)
  if not $allowed
    bot refuse to respond
    stop

define subflow {TOOL_INPUT_RAIL}
  $allowed = execute check_invoice_tool_result(tool_name=$tool_name, tool_message=$tool_message, invoice_kind=$invoice_kind)
  if not $allowed
    bot refuse to respond
    stop
'''

SELF_CHECK_OUTPUT_PROMPT = '''You are checking a response written by an invoice-incident agent before it is used.
The agent may only diagnose invoice duplicates and propose the registered operations
invoice.quarantine-duplicates.v1, invoice.rebuild-total.v1, and invoice.activate-idempotent-import.v1.
Block a response that reveals credentials, tokens, or connection strings; instructs a
reviewer or another system to bypass approval, broker checks, or policy; asks to contact
another destination; contains arbitrary SQL or shell commands; or claims a database
repair, execution, or verification happened. Allow ordinary evidence-based diagnosis,
rationale, risks, and no-change decisions.
Agent response: {{ bot_response }}
Should this response be blocked? Answer only Yes or No.'''


def rails_configuration():
    """Return the output and execution rails that extend the invoice input rail."""
    return {
        'output': {'flows': [OUTPUT_RAIL, 'self check output']},
        'tool_output': {'flows': [TOOL_OUTPUT_RAIL]},
        'tool_input': {'flows': [TOOL_INPUT_RAIL]},
        'config': {'regex_detection': {'output': {'patterns': list(SECRET_PATTERNS)}}},
    }


def contains_secret(text):
    return any(re.search(pattern, text) for pattern in SECRET_PATTERNS)


async def check_invoice_tool_calls(tool_calls=None, invoice_kind=None, scenario_id=None):
    """Execution rail on model output: one role-permitted call with contract-valid arguments."""
    if invoice_kind not in ROLE_TOOLS or not isinstance(tool_calls, list) or len(tool_calls) != 1:
        return False
    for call in tool_calls:
        name, raw = call.get('name'), call.get('arguments')
        if name not in ROLE_TOOLS[invoice_kind] or not isinstance(raw, str):
            return False
        if len(raw) > MAX_TOOL_ARGUMENTS or contains_secret(raw):
            return False
        try:
            arguments = json.loads(raw or '{}')
        except ValueError:
            return False
        contract = TOOL_ARGUMENTS[name]
        if contract is None:
            if arguments != {}:
                return False
            continue
        try:
            value = contract.model_validate(arguments)
        except ValidationError:
            return False
        steps = value.steps if isinstance(value, PlanningDecision) else (value,)
        if any(step.target != scenario_id for step in steps):
            return False
    return True


async def check_invoice_tool_result(tool_name=None, tool_message=None, invoice_kind=None):
    """Execution rail on tool results: bounded JSON from a role-permitted tool, without secrets."""
    if tool_name not in ROLE_TOOLS.get(invoice_kind, ()):
        return False
    if not isinstance(tool_message, str) or len(tool_message) > MAX_TOOL_RESULT or contains_secret(tool_message):
        return False
    try:
        json.loads(tool_message)
    except ValueError:
        return False
    return True


def register_actions(rails):
    rails.register_action(check_invoice_tool_calls, name='check_invoice_tool_calls')
    rails.register_action(check_invoice_tool_result, name='check_invoice_tool_result')


def rail_blocked(response, flow):
    """True unless the named rail ran and did not refuse; a rail that never ran is not a pass."""
    activated = [rail for rail in response.log.activated_rails if rail.name == flow]
    return not activated or any(REFUSAL in rail.decisions for rail in activated)
