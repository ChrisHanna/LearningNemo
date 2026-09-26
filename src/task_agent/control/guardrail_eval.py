"""Labeled evaluation of the invoice gateway's NeMo Guardrails.

Offline mode needs no model: it scores every case decided by a deterministic rail
(secret detection, tool-call contracts, tool-result checks) and every allow case on
those surfaces, with the model-backed rails stubbed to pass. Live mode sends every
case through the real APIM guardrail route and scores all of them, including the
model-backed input, topical, and output checks.
"""

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from task_agent.control import invoice_rails


ROOT = Path(__file__).parents[3]
CASES = ROOT / 'evals' / 'guardrails' / 'cases.yml'
SCENARIO = 'b' * 32
OTHER_SCENARIO = 'c' * 32
SURFACES = ('input', 'output', 'tool_call', 'tool_result')
DETERMINISTIC_RAILS = {invoice_rails.OUTPUT_RAIL, invoice_rails.TOOL_OUTPUT_RAIL, invoice_rails.TOOL_INPUT_RAIL}
MODEL_RAILS = {'self check input', invoice_rails.TOPIC_RAIL, 'self check output'}


@dataclass
class Result:
    case: dict
    blocked: bool
    seconds: float

    @property
    def correct(self):
        return self.blocked == (self.case['expect'] == 'block')


@dataclass
class Report:
    mode: str
    results: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    @property
    def mismatches(self):
        return [result for result in self.results if not result.correct]


def load_cases(path=CASES):
    cases = yaml.safe_load(Path(path).read_text(encoding='utf-8'))['cases']
    ids = [case['id'] for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError('case IDs must be unique')
    for case in cases:
        if case['surface'] not in SURFACES or case['expect'] not in ('allow', 'block'):
            raise ValueError(f"case {case['id']} has an unknown surface or expectation")
        if case['expect'] == 'block' and case.get('rail') not in DETERMINISTIC_RAILS | MODEL_RAILS:
            raise ValueError(f"block case {case['id']} must name the rail expected to catch it")
    return cases


def scored_offline(case):
    """Offline results mean something only where a deterministic rail makes the decision."""
    if case['surface'] == 'input':
        return False
    return case['expect'] == 'allow' or case['rail'] in DETERMINISTIC_RAILS


def _scenario(value):
    if isinstance(value, str):
        return {'SCENARIO': SCENARIO, 'OTHER_SCENARIO': OTHER_SCENARIO}.get(value, value)
    if isinstance(value, list):
        return [_scenario(item) for item in value]
    if isinstance(value, dict):
        return {key: _scenario(item) for key, item in value.items()}
    return value


def _arguments(case):
    value = case.get('arguments', {})
    return value if isinstance(value, str) else json.dumps(_scenario(value))


async def run_case(gateway, case):
    """Return True when the gateway blocks the case."""
    surface = case['surface']
    if surface == 'input':
        return not await gateway.check([{'role': case.get('role', 'user'), 'content': case['content']}],
                                       kind=case.get('kind', 'planning'))
    if surface == 'output':
        return not await gateway.check_response(case['content'], [], kind=case.get('kind', 'planning'), scenario_id=SCENARIO)
    if surface == 'tool_call':
        call = {'id': 'call-1', 'name': case['tool'], 'arguments': _arguments(case)}
        return not await gateway.check_response('', [call], kind=case['kind'], scenario_id=SCENARIO)
    messages = [{'role': 'user', 'content': 'Investigate the invoice incident'},
                {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'call-1', 'type': 'function', 'function': {'name': case['tool'], 'arguments': '{}'}}]},
                {'role': 'tool', 'tool_call_id': 'call-1', 'content': case['content']}]
    return not await gateway.check_tool_results(messages, kind=case['kind'])


async def evaluate(gateway, cases, *, mode):
    report = Report(mode=mode)
    for case in cases:
        if mode == 'offline' and not scored_offline(case):
            report.skipped.append(case)
            continue
        started = time.perf_counter()
        blocked = await run_case(gateway, case)
        report.results.append(Result(case, blocked, time.perf_counter() - started))
    return report


def render(report):
    """Markdown summary: accuracy by category, false positives and negatives, and latency."""
    lines = [f'## Guardrail evaluation ({report.mode})', '',
             '| Category | Cases | Correct | Accuracy |', '| --- | --- | --- | --- |']
    categories = {}
    for result in report.results:
        categories.setdefault(result.case['category'], []).append(result)
    for name, results in sorted(categories.items()):
        correct = sum(result.correct for result in results)
        lines.append(f'| {name} | {len(results)} | {correct} | {correct / len(results):.0%} |')
    total, correct = len(report.results), sum(result.correct for result in report.results)
    allowed = [result for result in report.results if result.case['expect'] == 'allow']
    blocked = [result for result in report.results if result.case['expect'] == 'block']
    false_positives = [result for result in allowed if result.blocked]
    false_negatives = [result for result in blocked if not result.blocked]
    lines += ['', f'- Scored: {total}, correct: {correct}' + (f' ({correct / total:.0%})' if total else ''),
              f'- False positives (benign blocked): {len(false_positives)} of {len(allowed)}',
              f'- False negatives (attacks allowed): {len(false_negatives)} of {len(blocked)}']
    if report.mode == 'live':
        for surface in SURFACES:
            seconds = sorted(result.seconds for result in report.results if result.case['surface'] == surface)
            if seconds:
                p95 = seconds[min(len(seconds) - 1, round(0.95 * (len(seconds) - 1)))]
                lines.append(f'- Latency {surface}: p50 {statistics.median(seconds) * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms')
    if report.skipped:
        lines.append(f'- Not scored offline (need a live model): {len(report.skipped)} cases; run with --live')
    for result in report.mismatches:
        verdict = 'blocked' if result.blocked else 'allowed'
        lines.append(f"- MISMATCH {result.case['id']}: expected {result.case['expect']}, {verdict}")
    return '\n'.join(lines)


def offline_gateway():
    """Real rails configuration with the check model stubbed to pass, so only deterministic rails decide."""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_openai import ChatOpenAI
    from task_agent.control.invoice_model import InvoiceModelGateway, build_guardrails

    async def generate(_self, messages, **_kwargs):
        topical = any(invoice_rails.TOPIC_CONTROL_PROMPT in str(message.content) for message in messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='on-topic' if topical else 'No'))])

    ChatOpenAI._agenerate = generate
    rails = build_guardrails(base_url='https://apim-nemo-8370187d.azure-api.net/guardrails', api_key='offline-evaluation')
    return InvoiceModelGateway(origin='https://apim-nemo-8370187d.azure-api.net/llm/v1', client=None, api_key='offline', rails=rails)


def live_gateway(*, base_url, api_key):
    from task_agent.control.invoice_model import InvoiceModelGateway, build_guardrails
    rails = build_guardrails(base_url=base_url, api_key=api_key)
    return InvoiceModelGateway(origin='https://apim-nemo-8370187d.azure-api.net/llm/v1', client=None, api_key='unused', rails=rails)
