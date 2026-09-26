"""The labeled guardrail evaluation set and its offline scoring."""

import pytest

from task_agent.control import guardrail_eval, invoice_rails


def test_cases_cover_every_surface_rail_and_expectation():
    cases = guardrail_eval.load_cases()
    assert {case['surface'] for case in cases} == set(guardrail_eval.SURFACES)
    caught = {case['rail'] for case in cases if case['expect'] == 'block'}
    assert caught == guardrail_eval.DETERMINISTIC_RAILS | guardrail_eval.MODEL_RAILS
    assert {case['expect'] for case in cases if case['surface'] == 'input'} == {'allow', 'block'}
    assert any(case['rail'] == invoice_rails.TOPIC_RAIL for case in cases if case['expect'] == 'block')


def test_block_case_must_name_its_rail(tmp_path):
    path = tmp_path / 'cases.yml'
    path.write_text('cases:\n  - {id: a, surface: output, expect: block, category: x, content: y}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='must name the rail'):
        guardrail_eval.load_cases(path)


def test_model_decided_cases_are_not_scored_offline():
    cases = {case['id']: case for case in guardrail_eval.load_cases()}
    assert not guardrail_eval.scored_offline(cases['input-incident'])
    assert not guardrail_eval.scored_offline(cases['output-false-success'])
    assert guardrail_eval.scored_offline(cases['output-diagnosis'])
    assert guardrail_eval.scored_offline(cases['call-wrong-role'])


@pytest.mark.asyncio
async def test_offline_evaluation_scores_deterministic_rails_without_mismatch(monkeypatch):
    from langchain_openai import ChatOpenAI
    monkeypatch.setattr(ChatOpenAI, '_agenerate', ChatOpenAI._agenerate)  # restored after the stub
    report = await guardrail_eval.evaluate(guardrail_eval.offline_gateway(), guardrail_eval.load_cases(), mode='offline')
    assert report.results and not report.mismatches
    assert any(result.case['expect'] == 'block' for result in report.results)
    assert all(case['surface'] == 'input' or case['rail'] in guardrail_eval.MODEL_RAILS for case in report.skipped)
    text = guardrail_eval.render(report)
    assert 'False positives (benign blocked): 0' in text and 'run with --live' in text


@pytest.mark.asyncio
async def test_a_wrong_label_is_reported_as_a_mismatch(monkeypatch):
    from langchain_openai import ChatOpenAI
    monkeypatch.setattr(ChatOpenAI, '_agenerate', ChatOpenAI._agenerate)
    case = {'id': 'mislabeled', 'surface': 'output', 'expect': 'allow', 'category': 'benign',
            'content': 'Password=hunter2'}
    report = await guardrail_eval.evaluate(guardrail_eval.offline_gateway(), [case], mode='offline')
    assert [result.case['id'] for result in report.mismatches] == ['mislabeled']
    assert 'MISMATCH mislabeled: expected allow, blocked' in guardrail_eval.render(report)
