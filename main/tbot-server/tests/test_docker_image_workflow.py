from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_ghcr_image_repository_is_normalized_to_lowercase():
    workflow = (REPO_ROOT / ".github/workflows/docker-image.yml").read_text(
        encoding="utf-8"
    )

    assert 'IMAGE_REPOSITORY=${GITHUB_REPOSITORY,,}' in workflow
    assert "env.IMAGE_REPOSITORY" in workflow
    assert "github.repository, env.VERSION" not in workflow
