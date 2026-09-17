from pathlib import Path

import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "backend-ci-cd.yml"
)


def test_release_is_calculated_once_and_reused():
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert "prepare-release" in jobs
    outputs = jobs["prepare-release"]["outputs"]
    assert outputs["release_tag"] == "${{ steps.calc.outputs.release_tag }}"
    assert outputs["commit_sha"] == "${{ steps.calc.outputs.commit_sha }}"
    assert (
        "calculate next release version" not in yaml.dump(jobs["deploy"]).lower()
        and "calculate next release version" not in yaml.dump(jobs["release"]).lower()
    )

    deploy_run = "\n".join(
        step.get("run", "") for step in jobs["deploy"]["steps"] if "run" in step
    )
    assert "deploy-sloty-backend ${COMMIT_SHA} ${RELEASE_TAG}" in deploy_run
    assert "needs.prepare-release.outputs.release_tag" in yaml.dump(jobs["deploy"])
    assert "needs.prepare-release.outputs.commit_sha" in yaml.dump(jobs["deploy"])

    release_env = None
    for step in jobs["release"]["steps"]:
        if step.get("name") == "Create GitHub release":
            release_env = step["env"]
    assert release_env["RELEASE_TAG"] == (
        "${{ needs.prepare-release.outputs.release_tag }}"
    )
    assert release_env["COMMIT_SHA"] == (
        "${{ needs.prepare-release.outputs.commit_sha }}"
    )
    assert jobs["release"]["needs"] == ["prepare-release", "deploy"]
