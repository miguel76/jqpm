import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
JQPM = REPO_ROOT / "jqpm.py"

# let unit tests `import jqpm` directly
sys.path.insert(0, str(REPO_ROOT))


def _git(args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test",
         "-c", "init.defaultBranch=main", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )


class FixtureRepo:
    """Handle onto a local git repo used as a jqpm dependency source."""

    def __init__(self, repo_dir):
        self.dir = repo_dir
        self.url = f"file://{repo_dir}"

    def tag(self, name, jq_body=None):
        """Add a new commit (optionally updating the entry file) and tag it."""
        if jq_body is not None:
            entry = self.dir / f"{self.dir.name}.jq"
            entry.write_text(jq_body)
            _git(["add", "-A"], cwd=self.dir)
            _git(["commit", "--quiet", "-m", f"tag {name}"], cwd=self.dir)
        _git(["tag", name], cwd=self.dir)

    def __str__(self):
        return self.url


@pytest.fixture
def make_repo(tmp_path):
    """
    Factory for a local git repo usable as a jqpm dependency source, with no
    network / GitHub involved. Repos are laid out at
    tmp_path/remotes/<owner>/<repo>, so a `file://` URL to one has the same
    last-two-path-segments shape jqpm expects for owner/repo.

    make_repo("acme", "strhelp", tags=["v1.0.0", "v1.1.0"]) -> FixtureRepo
    (str()'s to its file:// URL, so it drops straight into f-strings; use
    `.tag(...)` to add further tags to the same repo later.)
    """

    def _make(owner, repo, jq_body='def hello: "hi";\n', tags=(),
              entry=True, extra_files=None, branch="main"):
        repo_dir = tmp_path / "remotes" / owner / repo
        repo_dir.mkdir(parents=True)
        _git(["init", "--quiet", "-b", branch], cwd=repo_dir)
        if entry:
            (repo_dir / f"{repo}.jq").write_text(jq_body)
        for fname, content in (extra_files or {}).items():
            (repo_dir / fname).write_text(content)
        _git(["add", "-A"], cwd=repo_dir)
        _git(["commit", "--quiet", "-m", "initial"], cwd=repo_dir)
        fixture = FixtureRepo(repo_dir)
        for tag in tags:
            fixture.tag(tag)
        return fixture

    return _make


@pytest.fixture
def project(tmp_path):
    proj_dir = tmp_path / "project"
    proj_dir.mkdir()
    return proj_dir


@pytest.fixture
def jqpm(project):
    """Run the jqpm CLI as a subprocess in an isolated project dir."""

    def _run(*args, check=True):
        result = subprocess.run(
            [sys.executable, str(JQPM), *args],
            cwd=project, capture_output=True, text=True,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"jqpm {' '.join(args)} failed (exit {result.returncode})\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        return result

    _run.project = project
    return _run
