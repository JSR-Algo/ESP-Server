from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_ghcr_image_repository_is_normalized_to_lowercase():
    workflow = (REPO_ROOT / ".github/workflows/docker-image.yml").read_text(
        encoding="utf-8"
    )

    assert 'IMAGE_REPOSITORY=${GITHUB_REPOSITORY,,}' in workflow
    assert "env.IMAGE_REPOSITORY" in workflow
    assert "github.repository, env.VERSION" not in workflow


def test_base_image_repository_is_normalized_to_lowercase():
    workflow = (REPO_ROOT / ".github/workflows/build-base-image.yml").read_text(
        encoding="utf-8"
    )

    assert 'IMAGE_REPOSITORY=${GITHUB_REPOSITORY,,}' in workflow
    assert "tags: ghcr.io/${{ env.IMAGE_REPOSITORY }}:server-base" in workflow
    assert "tags: ghcr.io/${{ github.repository }}:server-base" not in workflow


def test_server_release_uses_base_image_from_current_repository():
    workflow = (REPO_ROOT / ".github/workflows/docker-image.yml").read_text(
        encoding="utf-8"
    )

    assert (
        "TBOT_SERVER_BASE_IMAGE=ghcr.io/${{ env.IMAGE_REPOSITORY }}:server-base"
        in workflow
    )


def test_server_release_only_follows_a_successful_base_build():
    workflow = (REPO_ROOT / ".github/workflows/docker-image.yml").read_text(
        encoding="utf-8"
    )

    assert "github.event.workflow_run.conclusion == 'success'" in workflow


def test_server_release_tags_the_runtime_image_with_the_exact_commit_sha():
    workflow = (REPO_ROOT / ".github/workflows/docker-image.yml").read_text(
        encoding="utf-8"
    )

    assert "ghcr.io/${{ env.IMAGE_REPOSITORY }}:server_${{ env.RELEASE_SHA }}" in workflow


def test_workflow_run_checks_out_and_tags_the_same_head_sha():
    workflow = (REPO_ROOT / ".github/workflows/docker-image.yml").read_text(
        encoding="utf-8"
    )
    release_sha = (
        "${{ github.event_name == 'workflow_run' && "
        "github.event.workflow_run.head_sha || github.sha }}"
    )

    assert f"ref: {release_sha}" in workflow
    assert f"RELEASE_SHA={release_sha}" in workflow


def test_runtime_dockerfile_defaults_to_the_current_lowercase_base_image():
    dockerfile = (REPO_ROOT / "Dockerfile-server").read_text(encoding="utf-8")

    assert (
        "ARG TBOT_SERVER_BASE_IMAGE=ghcr.io/jsr-algo/esp-server:server-base"
        in dockerfile
    )
    assert "xinnan-tech/tbot-esp32-server" not in dockerfile
