from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_human_image_is_separate_pinned_and_nonroot():
    dockerfile = (ROOT / "containers/cloud-human.Dockerfile").read_text()
    assert "@sha256:" in dockerfile.splitlines()[0]
    assert "--require-hashes" in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert "import mssql_python" in dockerfile
    assert "deployed_incident_app" in dockerfile and "deployed_review_app" in dockerfile
    assert ".nemo-test-client.json" not in dockerfile
    assert "COPY . " not in dockerfile
    console = (ROOT / "containers/cloud-console.Dockerfile").read_text()
    assert "COPY src/task_agent/control" not in console


def test_human_services_default_to_identity_staging_and_private_ingress():
    source = (ROOT / "infra/next-phase/human-services.bicep").read_text()
    assert "param deployApps bool = false" in source
    assert "if (deployApps)" in source
    assert "external: false" in source and "allowInsecure: false" in source
    assert "var services = ['incident', 'review', 'execution']" in source
    assert "minReplicas: 1, maxReplicas: 1" in source
    assert "secrets: []" in source
    assert "Microsoft.Authorization/roleAssignments" not in source


def test_build_selection_keeps_human_services_opt_in():
    source = (ROOT / "scripts/build-cloud-demo.sh").read_text()
    assert "all) kinds=(console agent)" in source
    assert "human) kinds=(human)" in source
    assert "containers/human-services.requirements.lock" in source


def test_dashboard_handoff_configuration_is_explicit_and_private():
    template = (ROOT / "infra/next-phase/cloud-demo.bicep").read_text()
    assert "param incidentOrigin string = ''" in template
    assert "param reviewOrigin string = ''" in template
    script = (ROOT / "infra/next-phase/deploy-cloud-demo.sh").read_text()
    assert "LEARNINGNEMO_HUMAN_SERVICES_VERIFIED" in script
    assert "assert origin==expected" in script
    assert "-dev.internal." in script