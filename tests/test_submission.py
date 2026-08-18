"""The submission webhook, in six sections.

Routing, idempotence and guards drive service.py through FakeGitHub. The HTTP
layer goes through TestClient. Configuration covers what app.py reads at
startup. The last section is the only one to exercise the real GitHub client,
with urlopen replaced, and it is where the transport and the status codes are
pinned down."""

import base64
import io
import json
import urllib.error
import urllib.request

import pytest
from app import Settings, _from_environment, app
from fastapi.testclient import TestClient
from github_client import GitHubClient, GitHubError
from service import (
    SubmissionConfig,
    SubmissionError,
    handle_submission,
)

CONFIG = SubmissionConfig(github_owner="Pierrott64", registry_repo="dmp-registry")
DSW_URL = "http://localhost:8080/wizard/projects/7c42caa4-a0e0-4112-9623-4334641c457a"
DMP_PATH = "projects/glider/template/dmp_glider_template.json"
META_PATH = "projects/glider/template/dmp_glider_template.meta.json"
RAW_URL = f"https://raw.githubusercontent.com/Pierrott64/dmp-registry/main/{DMP_PATH}"
ENVELOPE = {
    "project": "glider",
    "template_version": "1.0.0",
    "rules": [{"rda_dcs": "1.0.0"}, {"ostrails": "1.0.0"}],
}


def _document(title="Glider mission DMP", identifier=DSW_URL, envelope=None):
    """What a maDMP document template renders: the dmp object, and beside it
    the provenance block the webhook takes back out."""
    document = {
        "dmp": {"title": title, "dmp_id": {"identifier": identifier, "type": "url"}}
    }
    envelope = ENVELOPE if envelope is None else envelope
    if envelope is not ...:
        document["metadata"] = json.loads(json.dumps(envelope))
    return document


class FakeGitHub:
    """In-memory stand-in for GitHubClient: get_file and commit_files over one
    registry repo (path -> bytes).

    A commit is recorded as the paths it carried and its message, so a test
    can assert that several files travelled together and not one after the
    other.
    """

    def __init__(self, files: dict[str, bytes] | None = None):
        self.files = dict(files or {})
        self.commits: list[tuple[list[str], str]] = []

    def get_file(self, owner, repo, path):
        if path not in self.files:
            return None
        return {
            "sha": f"sha-{len(self.files[path])}",
            "content": base64.b64encode(self.files[path]).decode(),
        }

    def commit_files(self, owner, repo, branch, files, message):
        self.files.update(files)
        self.commits.append((sorted(files), message))
        return "new-commit-sha"


def _initialized(folder="glider") -> FakeGitHub:
    """A registry where the project's folder has already been laid out."""
    return FakeGitHub({f"projects/{folder}/template/.gitkeep": b""})


# Routing + dmp_id rewrite


def test_first_submit_writes_dmp_and_rewrites_dmp_id():
    github = _initialized("glider")
    result = handle_submission(_document(), "glider", github, CONFIG)
    assert result["action"] == "created"
    assert result["file"] == DMP_PATH
    stored = json.loads(github.files[DMP_PATH])
    # dmp_id was the DSW placeholder, the webhook rewrote it to the registry URL.
    assert stored["dmp"]["dmp_id"] == {"identifier": RAW_URL, "type": "url"}
    assert stored["dmp"]["title"] == "Glider mission DMP"


def test_folder_only_touches_its_own_path():
    github = _initialized("glider")
    handle_submission(_document(), "glider", github, CONFIG)
    assert [paths for paths, _ in github.commits] == [[DMP_PATH, META_PATH]]


def test_commit_message_names_the_project():
    """Every project commits into the same repository, so `git log` needs the
    folder in the message to be readable at all."""
    github = _initialized("glider")
    handle_submission(_document(), "glider", github, CONFIG)
    handle_submission(_document(title="Renamed"), "glider", github, CONFIG)
    assert [message for _, message in github.commits] == [
        "Add DMP for glider (DSW submission)",
        "Update DMP for glider (DSW submission)",
    ]


# The provenance envelope


def test_the_envelope_leaves_the_dmp_and_lands_beside_it():
    """The whole point of the block: what the registry holds is RDA DCS and
    nothing else, and the versions it was built from sit next to it, written
    by the same commit so the two can never disagree."""
    github = _initialized()
    result = handle_submission(_document(), "glider", github, CONFIG)
    assert result["metadata"] == META_PATH
    assert "metadata" not in json.loads(github.files[DMP_PATH])
    assert json.loads(github.files[META_PATH]) == ENVELOPE


def test_the_envelope_travels_in_the_commit_that_carries_the_dmp():
    """One commit, both files. Two commits would let the second fail and
    leave a DMP whose rules versions nobody knows."""
    github = _initialized()
    handle_submission(_document(), "glider", github, CONFIG)
    assert len(github.commits) == 1
    assert github.commits[0][0] == [DMP_PATH, META_PATH]


def test_a_document_without_an_envelope_is_refused():
    """A DMP whose rules versions are unknown cannot be checked against them,
    and a folder holding one would have to be cleaned up by hand."""
    github = _initialized()
    with pytest.raises(SubmissionError, match="carries no 'metadata' object"):
        handle_submission(_document(envelope=...), "glider", github, CONFIG)
    assert github.commits == []


def test_an_envelope_naming_another_project_is_refused():
    """The folder comes from the service URL and the project name from the
    template. They disagreeing means the document was submitted through
    somebody else's service, and it must not land in that folder."""
    envelope = {**ENVELOPE, "project": "canales"}
    with pytest.raises(SubmissionError, match="generated for project 'canales'"):
        handle_submission(
            _document(envelope=envelope), "glider", _initialized(), CONFIG
        )


@pytest.mark.parametrize(
    "rules",
    [
        pytest.param(None, id="absent"),
        pytest.param([], id="empty"),
        pytest.param("rda_dcs 1.0.0", id="a string"),
        pytest.param([{"rda_dcs": "1.0.0", "ostrails": "1.0.0"}], id="two keys in one"),
        pytest.param([{"rda_dcs": 1.0}], id="an unquoted version"),
        pytest.param([{"rda_dcs": ""}], id="an empty version"),
    ],
)
def test_malformed_pins_are_refused(rules):
    """Quality control resolves these into file paths, so a shape it cannot
    read has to be caught here, while there is still somebody to tell."""
    envelope = {**ENVELOPE, "rules": rules}
    github = _initialized()
    with pytest.raises(SubmissionError, match="metadata.rules"):
        handle_submission(_document(envelope=envelope), "glider", github, CONFIG)
    assert github.commits == []


# Idempotence


def test_resubmit_same_content_commits_nothing():
    github = _initialized()
    handle_submission(_document(), "glider", github, CONFIG)
    before = list(github.commits)
    result = handle_submission(_document(), "glider", github, CONFIG)
    assert result["action"] == "unchanged"
    assert github.commits == before


def test_resubmit_of_an_already_rewritten_document_is_unchanged():
    """The second submission of the same DMP arrives with dmp_id already
    pointing at the registry, since that is what the first one wrote back.
    Rewriting it to the same value has to leave the content identical."""
    github = _initialized()
    handle_submission(_document(), "glider", github, CONFIG)
    result = handle_submission(_document(identifier=RAW_URL), "glider", github, CONFIG)
    assert result["action"] == "unchanged"


def test_resubmit_changed_content_updates():
    github = _initialized()
    handle_submission(_document(), "glider", github, CONFIG)
    before = len(github.commits)
    result = handle_submission(
        _document(title="Renamed project"), "glider", github, CONFIG
    )
    assert result["action"] == "updated"
    assert [paths for paths, _ in github.commits[before:]] == [[DMP_PATH, META_PATH]]


def test_a_new_template_version_alone_is_an_update():
    """The envelope is compared like the DMP is. A researcher who answered
    nothing new but migrated to a newer template has to leave a trace, that
    is the fact quality control reads."""
    github = _initialized()
    handle_submission(_document(), "glider", github, CONFIG)
    envelope = {**ENVELOPE, "template_version": "2.0.0"}
    result = handle_submission(_document(envelope=envelope), "glider", github, CONFIG)
    assert result["action"] == "updated"
    assert json.loads(github.files[META_PATH])["template_version"] == "2.0.0"


# Guards


def test_uninitialized_folder_refused():
    """No template/.gitkeep (the project was never registered) -> refuse,
    don't half-create."""
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
    """`app` is a module-level singleton, so its one seam is installed through
    monkeypatch, which puts the real builder back after every test."""
    settings = Settings(
        submission_token="s3cret", github=_initialized("glider"), config=CONFIG
    )
    monkeypatch.setattr(app.state, "build", lambda: settings)
    with TestClient(app) as test_client:
        yield test_client


def test_health_answers_when_the_webhook_is_configured(client):
    """The compose healthcheck polls this, so `up --wait` and the container's
    restart both hang on it answering."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_needs_no_token(client):
    """It is called by docker, which has no business holding the shared
    secret."""
    assert client.get("/health").status_code == 200


def test_http_rejects_bad_token(client):
    response = client.post(
        "/submissions?project=glider",
        content=json.dumps(_document()),
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_http_rejects_a_non_ascii_token(client):
    """The header is whatever the caller sent, and compare_digest refuses
    non-ASCII strings, so the comparison has to happen on bytes. Anything else
    turns a wrong token into a 500."""
    response = client.post(
        "/submissions?project=glider",
        content=json.dumps(_document()),
        # Raw bytes: httpx refuses to build a non-ASCII header from a str, but
        # nothing stops a client from putting these on the wire, and starlette
        # decodes them as latin-1.
        headers={"Authorization": "Bearer clé".encode("latin-1")},
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
    assert response.json()["action"] == "created"


def test_http_reports_a_refused_write_rather_than_success(monkeypatch):
    """What a revoked or mis-scoped token looks like: the registry reads fine
    and refuses the write. DSW has to see a failure, not a submission it will
    believe was filed."""

    class RefusingGitHub(FakeGitHub):
        def commit_files(self, *args, **kwargs):
            raise GitHubError(404, "Not Found")

    settings = Settings(
        submission_token="s3cret",
        github=RefusingGitHub({"projects/glider/template/.gitkeep": b""}),
        config=CONFIG,
    )
    monkeypatch.setattr(app.state, "build", lambda: settings)
    with TestClient(app) as test_client:
        response = test_client.post(
            "/submissions?project=glider",
            content=json.dumps(_document()),
            headers={
                "Authorization": "Bearer s3cret",
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 502


def test_http_bad_json_is_400(client):
    response = client.post(
        "/submissions?project=glider",
        content=b"not json",
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 400


# Configuration read from the environment
#
# Unset and empty are tested alike because compose always defines what its
# `environment:` block lists: a value absent from .env reaches the container as
# an empty string, not as a missing variable. Both must fail, and fail the same
# way, or the webhook commits somewhere nobody asked for, or serves with no
# shared secret at all.

ENVIRONMENT = {
    "SUBMISSION_TOKEN": "s3cret",
    "REGISTRY_TOKEN": "ghp_registry",
    "REGISTRY_OWNER": "Pierrott64",
    "REGISTRY_REPO": "dmp-registry",
}


@pytest.fixture()
def environment(monkeypatch):
    for name, value in ENVIRONMENT.items():
        monkeypatch.setenv(name, value)


def test_settings_read_the_environment(environment):
    settings = _from_environment()
    assert settings.submission_token == "s3cret"
    assert settings.github.token == "ghp_registry"
    assert settings.config == CONFIG


@pytest.mark.parametrize("missing", list(ENVIRONMENT))
def test_settings_refuse_an_unset_variable(environment, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match=missing):
        _from_environment()


@pytest.mark.parametrize("empty", list(ENVIRONMENT))
def test_settings_refuse_an_empty_variable(environment, monkeypatch, empty):
    monkeypatch.setenv(empty, "")
    with pytest.raises(RuntimeError, match=empty):
        _from_environment()


def test_every_unset_variable_is_named_at_once(monkeypatch):
    """One pass to fix a fresh deployment, not one restart per variable."""
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError) as raised:
        _from_environment()
    assert all(name in str(raised.value) for name in ENVIRONMENT)


def test_startup_refuses_an_incomplete_environment(monkeypatch):
    """The webhook does not serve at all, where reading the environment per
    request would let it answer /health and fail on the first submission."""
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="SUBMISSION_TOKEN"), TestClient(app):
        pass  # pragma: no cover


# The GitHub client
#
# Everything above runs against FakeGitHub, so these are the only tests that
# reach the real client: the request it builds, and what it makes of a status
# code. urlopen is replaced, nothing here touches the network.


class _Reply:
    """Shaped like urlopen's return value: a context manager that reads once."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_urlopen(monkeypatch, *, status=200, payload=b"{}"):
    """Answers with `status`, raising HTTPError above 400 the way urllib does.
    Returns a dict where the request that was built lands, so a test can look
    at what would have gone over the wire."""
    seen: dict[str, urllib.request.Request] = {}

    def stub(request, timeout=None):
        seen["request"] = request
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, status, "", {}, io.BytesIO(b'{"message": "nope"}')
            )
        return _Reply(payload)

    monkeypatch.setattr(urllib.request, "urlopen", stub)
    return seen


def test_get_file_reads_a_404_as_absent(monkeypatch):
    _stub_urlopen(monkeypatch, status=404)
    assert GitHubClient("token").get_file("owner", "repo", "path") is None


def test_get_file_raises_on_any_other_error(monkeypatch):
    _stub_urlopen(monkeypatch, status=403)
    with pytest.raises(GitHubError) as raised:
        GitHubClient("token").get_file("owner", "repo", "path")
    assert raised.value.status == 403


def test_a_commit_treats_a_404_as_a_failure(monkeypatch):
    """A 404 on a write means the write did not happen. Reading it as an
    absence, the way a GET does, let a submission that wrote nothing answer
    200 with `"action": "created"` and a link to a file that was never there."""
    _stub_urlopen(monkeypatch, status=404)
    with pytest.raises(GitHubError) as raised:
        GitHubClient("token").commit_files("o", "r", "main", {"p": b"x"}, "message")
    assert raised.value.status == 404


def test_unreachable_github_is_the_same_error_without_a_status(monkeypatch):
    """DNS down, connection refused, timeout: never a response, so no status.
    Same exception as a refusal, so app.py answers 502 rather than letting an
    OSError out as a 500."""

    def stub(request, timeout=None):
        raise urllib.error.URLError("nodename nor servname provided")

    monkeypatch.setattr(urllib.request, "urlopen", stub)
    with pytest.raises(GitHubError) as raised:
        GitHubClient("token").get_file("owner", "repo", "path")
    assert raised.value.status is None
    assert "unreachable" in str(raised.value)


def _stub_git_data(monkeypatch):
    """A GitHub that answers the four calls a commit makes, and hands back
    every request that was built, in order."""
    seen: list[urllib.request.Request] = []
    answers = {
        "/branches/main": {
            "commit": {"sha": "parent", "commit": {"tree": {"sha": "base"}}}
        },
        "/git/trees": {"sha": "new-tree"},
        "/git/commits": {"sha": "new-commit"},
        "/git/refs/heads/main": {},
    }

    def stub(request, timeout=None):
        seen.append(request)
        for suffix, payload in answers.items():
            if request.full_url.endswith(suffix):
                return _Reply(json.dumps(payload).encode())
        raise AssertionError(f"unexpected call {request.full_url}")

    monkeypatch.setattr(urllib.request, "urlopen", stub)
    return seen


def test_a_commit_builds_a_tree_then_moves_the_branch(monkeypatch):
    """The order is what makes the write atomic: the files go into a tree and
    a commit, neither of which anything points at, and one reference move
    publishes them all at once."""
    seen = _stub_git_data(monkeypatch)
    sha = GitHubClient("token").commit_files(
        "o", "r", "main", {"a/one.json": b"1", "a/two.json": b"2"}, "msg"
    )
    assert sha == "new-commit"
    assert [r.get_method() for r in seen] == ["GET", "POST", "POST", "PATCH"]
    assert [r.full_url.split("/repos/o/r")[1] for r in seen] == [
        "/branches/main",
        "/git/trees",
        "/git/commits",
        "/git/refs/heads/main",
    ]
    tree = json.loads(seen[1].data)
    assert tree["base_tree"] == "base"
    assert {entry["path"]: entry["content"] for entry in tree["tree"]} == {
        "a/one.json": "1",
        "a/two.json": "2",
    }
    assert all(entry["mode"] == "100644" for entry in tree["tree"])
    commit = json.loads(seen[2].data)
    assert (commit["tree"], commit["parents"], commit["message"]) == (
        "new-tree",
        ["parent"],
        "msg",
    )
    assert json.loads(seen[3].data)["sha"] == "new-commit"
    assert seen[0].headers["Authorization"] == "Bearer token"
