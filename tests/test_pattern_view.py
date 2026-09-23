from html.parser import HTMLParser
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


def test_guided_demo_is_default_and_controls_have_real_destinations():
    parser = Elements()
    parser.feed((ROOT / 'src/task_agent/console/static/index.html').read_text())
    nodes = {attrs['id']: attrs for tag, attrs in parser.nodes if 'id' in attrs}
    ids = [attrs['id'] for tag, attrs in parser.nodes if 'id' in attrs]
    assert len(ids) == len(set(ids))
    assert 'hidden' not in nodes['missionWorkspace']
    assert 'hidden' in nodes['patternWorkspace']
    assert nodes['missionEvidenceDrawer']['aria-labelledby'] == 'missionDrawerTitle'
    assert 'hidden' in nodes['liveWorkspace']
    assert nodes['patternViewTab']['aria-selected'] == 'true'
    assert nodes['liveViewTab']['aria-selected'] == 'false'
    for tag, attrs in parser.nodes:
        for relationship in ('aria-controls', 'data-pattern-view'):
            if relationship in attrs:
                assert attrs[relationship] in nodes
    boundaries = [name for name in ids if name.startswith('boundary-')]
    assert len(boundaries) == 5
    assert {nodes[name]['aria-selected'] for name in boundaries} == {'true', 'false'}


def test_persona_design_does_not_execute_or_change_authentication():
    source = (ROOT / 'src/task_agent/console/static/pattern.js').read_text()
    assert all(f'id: "{name}"' in source for name in ('reader', 'operator', 'approver', 'agent'))
    for forbidden in ('fetch(', 'api(', 'state.session', 'localStorage', 'sessionStorage', 'innerHTML', 'liveRun', 'runScenarioButton'):
        assert forbidden not in source
    assert 'Target design' in source
    assert 'not this delegated AgentRunner workflow' in source


def test_console_only_build_rejects_unknown_selection_before_cloud_access():
    script = ROOT / 'scripts/build-cloud-demo.sh'
    result = subprocess.run(['bash', str(script), 'invalid'], capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert 'Expected all, console, agent, or human' in result.stderr
    source = script.read_text()
    assert 'console) kinds=(console)' in source
    assert 'cloud-$kind.source-sha256.txt' in source