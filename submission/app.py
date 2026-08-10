"""FastAPI wiring for the submission webhook.

Environment (all four come from .env via docker-compose's `environment:`):
    SUBMISSION_TOKEN   shared secret DSW sends as "Authorization: Bearer ..."
                       (configured as a static header in the DSW admin's
                       Submission Service form)
    REGISTRY_TOKEN     PAT with Contents RW on the dmp-registry repo
    REGISTRY_OWNER     account owning the registry (e.g. Pierrott64)
    REGISTRY_REPO      the mono-repo all projects live in (default: dmp-registry)

The REGISTRY_ prefix is deliberate rather than GITHUB_: compose resolves
`.env` values only when the shell does not already define the name, and
GITHUB_TOKEN is a name a developer's shell very often holds — a collision
would silently commit with the wrong credentials. GitHub also reserves the
GITHUB_ prefix for Codespaces Secrets, so this one name works everywhere.

Run:
    uvicorn app:app --host 0.0.0.0 --port 8080

DSW POSTs the rendered document to /submissions?project=<folder> — the
per-project folder (laid out ahead of time from madmp-core's `registry/`) is
carried in the query string, set on the DSW Submission Service's URL. The
body is a raw JSON document or a multipart upload; both are accepted.
"""

from __future__ import annotations

import json
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from github_client import GitHubClient, GitHubError
from service import SubmissionConfig, SubmissionError, handle_submission

app = FastAPI(title="maDMP submission webhook")


def _config() -> SubmissionConfig:
    # The field stays `github_owner` — it really is a GitHub account. Only the
    # environment variable is namespaced, to keep it collision-free.
    return SubmissionConfig(
        github_owner=os.environ["REGISTRY_OWNER"],
        registry_repo=os.environ.get("REGISTRY_REPO", "dmp-registry"),
    )


def _github() -> GitHubClient:
    return GitHubClient(token=os.environ["REGISTRY_TOKEN"])


# Swapped out by the tests (fake GitHub, fixed config) without touching env.
app.state.make_config = _config
app.state.make_github = _github


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/submissions")
async def submissions(request: Request) -> dict[str, str]:
    expected = os.environ.get("SUBMISSION_TOKEN")
    if not expected:
        raise HTTPException(500, "SUBMISSION_TOKEN is not configured")
    if request.headers.get("authorization") != f"Bearer {expected}":
        raise HTTPException(401, "invalid or missing submission token")

    folder = request.query_params.get("project")
    if not folder:
        raise HTTPException(400, "missing ?project=<folder> query parameter")

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/"):
        form = await request.form()
        uploads = [v for v in form.values() if hasattr(v, "read")]
        if not uploads:
            raise HTTPException(400, "multipart body carries no file")
        raw = await uploads[0].read()
    else:
        raw = await request.body()

    try:
        document = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "body is not valid JSON")

    try:
        result = handle_submission(
            document,
            folder,
            request.app.state.make_github(),
            request.app.state.make_config(),
        )
    except SubmissionError as e:
        raise HTTPException(400, str(e))
    except GitHubError as e:
        raise HTTPException(502, str(e))
    # DSW displays the Location header as a clickable link on the
    # submission — point it at the repository.
    return JSONResponse(result, headers={"Location": result["repository"]})
