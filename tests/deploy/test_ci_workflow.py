from pathlib import Path

import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "backend-ci-cd.yml"
)
WORKFLOW_TEXT = WORKFLOW_PATH.read_text(encoding="utf-8")


def test_release_is_calculated_once_and_reused():
    workflow = yaml.safe_load(WORKFLOW_TEXT)
    jobs = workflow["jobs"]

    assert "prepare-release" in jobs
    outputs = jobs["prepare-release"]["outputs"]
    assert list(outputs) == ["release_tag"]
    assert outputs["release_tag"] == "${{ steps.calc.outputs.release_tag }}"
    assert "resolve_production_release_tag" in WORKFLOW_TEXT
    assert (
        "calculate next release version" not in yaml.dump(jobs["deploy"]).lower()
        and "calculate next release version" not in yaml.dump(jobs["release"]).lower()
    )

    deploy_run = "\n".join(
        step.get("run", "") for step in jobs["deploy"]["steps"] if "run" in step
    )
    assert "/srv/sloty/backend/scripts/deploy-sloty-backend" in deploy_run
    assert "${{ github.sha }}" in deploy_run
    assert "${RELEASE_TAG}" in deploy_run
    assert "needs.prepare-release.outputs.release_tag" in yaml.dump(jobs["deploy"])
    assert "/usr/local/sbin" not in WORKFLOW_TEXT

    release_env = None
    for step in jobs["release"]["steps"]:
        if step.get("name") == "Create GitHub release":
            release_env = step["env"]
            release_run = step["run"]
    assert release_env["RELEASE_TAG"] == (
        "${{ needs.prepare-release.outputs.release_tag }}"
    )
    assert release_env["COMMIT_SHA"] == "${{ github.sha }}"
    assert "release_tag_sha_status" in release_run
    assert "conflict" in release_run
    assert jobs["release"]["needs"] == ["prepare-release", "deploy"]


def test_workflow_serializes_master_deployments():
    workflow = yaml.safe_load(WORKFLOW_TEXT)
    assert workflow["concurrency"]["group"] == (
        "${{ github.workflow }}-${{ github.ref }}"
    )
    assert workflow["concurrency"]["cancel-in-progress"] == (
        "${{ github.ref != 'refs/heads/master' }}"
    )


def test_release_waits_for_postgresql_concurrency_tests():
    jobs = yaml.safe_load(WORKFLOW_TEXT)["jobs"]
    postgresql = jobs["postgresql"]

    assert postgresql["services"]["postgres"]["image"] == "postgres:17"
    assert postgresql["env"]["DB_ENGINE"] == "postgresql"
    run = "\n".join(step.get("run", "") for step in postgresql["steps"])
    assert "pytest tests/test_postgresql_concurrency.py" in run
    assert jobs["prepare-release"]["needs"] == ["test", "postgresql"]


def test_workflow_validates_bash_syntax():
    assert "bash -n scripts/deploy-sloty-backend" in WORKFLOW_TEXT
    assert "bash -n scripts/write-backend-release-metadata" in WORKFLOW_TEXT
    assert "bash -n scripts/lib/backend-release.sh" in WORKFLOW_TEXT
