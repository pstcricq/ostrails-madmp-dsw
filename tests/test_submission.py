"""submission/: the webhook routes by ?project=<folder>, drops the DMP into
the registry folder's template/, rewrites dmp_id to the registry raw URL, is
idempotent, refuses an uninitialized folder or an unsafe folder name, and the
HTTP layer enforces the shared secret and the query parameter."""

import base64
import json

import pytest
from app import app
from fastapi.testclient import TestClient
from service import (
    SubmissionConfig,
    SubmissionError,
    handle_submission,
)

CONFIG = SubmissionConfig(
    github_owner="Pierrott64"
)  # registry_repo defaults to dmp-registry
DSW_URL = "http://localhost:8080/wizard/projects/7c42caa4-a0e0-4112-9623-4334641c457a"
DMP_PATH = "projects/glider/template/dmp_glider_template.json"
RAW_URL = f"https://raw.githubusercontent.com/Pierrott64/dmp-registry/main/{DMP_PATH}"


def _document(title="Glider mission DMP", identifier=DSW_URL):
    return {
        "dmp": {"title": title, "dmp_id": {"identifier": identifier, "type": "url"}}
    }


class FakeGitHub:
    """In-memory stand-in for GitHubClient: just get_file / put_file over one
    registry repo (path -> bytes)."""

    def __init__(self, files: dict[str, bytes] | None = None):
        self.files = dict(files or {})
        self.commits: list[tuple[str, str]] = []

    def get_file(self, owner, repo, path):
        if path not in self.files:
            return None
        return {
            "sha": f"sha-{len(self.files[path])}",
            "content": base64.b64encode(self.files[path]).decode(),
        }

    def put_file(self, owner, repo, path, content, message, sha=None):
        self.files[path] = content
        self.commits.append((path, message))
        return {}


def _initialized(folder="glider") -> FakeGitHub:
    """A registry where the project's folder has already been laid out."""
    return FakeGitHub({f"projects/{folder}/meta.yaml": b"id: glider\nrules: []\n"})


# Routing + dmp_id rewrite


def test_first_submit_writes_dmp_and_rewrites_dmp_id():
    github = _initialized("glider")
    result = handle_submission(_document(), "glider", github, CONFIG)
    assert result["action"] == "created"
    assert result["file"] == DMP_PATH
    stored = json.loads(github.files[DMP_PATH])
    # dmp_id was the DSW placeholder; the webhook rewrote it to the registry URL.
    assert stored["dmp"]["dmp_id"] == {"identifier": RAW_URL, "type": "url"}
    assert stored["dmp"]["title"] == "Glider mission DMP"


def test_folder_only_touches_its_own_path():
    github = _initialized("glider")
    handle_submission(_document(), "glider", github, CONFIG)
    assert [c[0] for c in github.commits] == [DMP_PATH]


# Idempotence


def test_resubmit_same_content_commits_nothing():
    github = _initialized()
    handle_submission(_document(), "glider", github, CONFIG)
    before = list(github.commits)
    result = handle_submission(_document(), "glider", github, CONFIG)
    assert result["action"] == "unchanged"
    assert github.commits == before


def test_resubmit_changed_content_updates():
    github = _initialized()
    handle_submission(_document(), "glider", github, CONFIG)
    before = len(github.commits)
    result = handle_submission(
        _document(title="Renamed project"), "glider", github, CONFIG
    )
    assert result["action"] == "updated"
    assert [c[0] for c in github.commits[before:]] == [DMP_PATH]


# Guards


def test_uninitialized_folder_refused():
    """No meta.yaml (the project was never registered) -> refuse, don't
    half-create."""
    github = FakeGitHub()
    with pytest.raises(SubmissionError, match="not initialized"):
        handle_submission(_document(), "glider", github, CONFIG)


@pytest.mark.parametrize("folder", ["", "../evil", "a/b", "UPPER", "dot.dot", "sp ace"])
def test_unsafe_folder_refused(folder):
    with pytest.raises(SubmissionError, match="invalid project folder"):
        handle_submission(_document(), folder, _initialized(), CONFIG)


def test_document_without_dmp_refused():
    with pytest.raises(SubmissionError, match="no dmp object"):
        handle_submission({"foo": 1}, "glider", _initialized(), CONFIG)


# HTTP layer


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SUBMISSION_TOKEN", "s3cret")
    github = _initialized("glider")
    app.state.make_github = lambda: github
    app.state.make_config = lambda: CONFIG
    with TestClient(app) as test_client:
        test_client.fake_github = github
        yield test_client


def test_http_rejects_bad_token(client):
    response = client.post(
        "/submissions?project=glider",
        content=json.dumps(_document()),
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_http_missing_project_is_400(client):
    response = client.post(
        "/submissions",
        content=json.dumps(_document()),
        headers={"Authorization": "Bearer s3cret", "Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_http_raw_json_body(client):
    response = client.post(
        "/submissions?project=glider",
        content=json.dumps(_document()),
        headers={"Authorization": "Bearer s3cret", "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "created"
    assert response.json()["folder"] == "glider"
    assert response.headers["location"] == response.json()["repository"]


def test_http_multipart_body(client):
    response = client.post(
        "/submissions?project=glider",
        files={"file": ("dmp.json", json.dumps(_document()), "application/json")},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    assert response.json()["action"] in ("created", "unchanged")


def test_http_bad_json_is_400(client):
    response = client.post(
        "/submissions?project=glider",
        content=b"not json",
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 400
