import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _git_ls(path: str) -> str:
    result = subprocess.run(
        ["git", "ls-files", path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def test_env_file_not_tracked():
    assert _git_ls(".env") == ""


def test_data_dir_not_tracked():
    assert _git_ls("data") == ""


def test_gitignore_covers_runtime_paths():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for path in ("data/competition/", "data/agent_learning/", "data/scorecards/", ".env"):
        assert path in gitignore
