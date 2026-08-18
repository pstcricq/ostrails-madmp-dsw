"""FastAPI wiring for the submission webhook.

DSW POSTs the rendered document to /submissions?project=<folder>, the folder
being carried in the query string of the Submission Service's URL. The body is
a raw JSON document or a multipart upload, both are accepted.

Four variables, all required and read once at startup:
    SUBMISSION_TOKEN   the secret DSW sends as "Authorization: Bearer ..."
    REGISTRY_TOKEN     the PAT this webhook commits with
    REGISTRY_OWNER     the account and the repository DMPs are written to
    REGISTRY_REPO

(!!) REGISTRY_ and not GITHUB_. Compose lets the shell override .env, and
GITHUB_TOKEN is a name a developer's shell often already holds, so that rename
would have the webhook commit with someone else's credentials, in silence.
"""

from __future__ import annotations

import hmac
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from github_client import GitHubClient, GitHubError
from service import SubmissionConfig, SubmissionError, handle_submission

REQUIRED = ("SUBMISSION_TOKEN", "REGISTRY_TOKEN", "REGISTRY_OWNER", "REGISTRY_REPO")


@dataclass(frozen=True)
class Settings:
    """What the webhook serves with, built once at startup and never rebuilt."""

    submission_token: str
    github: GitHubClient
    config: SubmissionConfig


def _from_environment() -> Settings:
    """The four variables, or one error naming every one of them that is unset.

    Missing and empty are the same failure and have to be treated alike:
    compose always defines what its `environment:` block lists, so a value
    absent from .env arrives here as an empty string rather than not at all. A
    default written in this file would therefore never apply, and would read as
    a guarantee it could not keep. The values' one home is .env.example.

    All four are reported together, so a fresh deployment is fixed in one pass
    instead of one restart per variable.
    """
    values = {name: os.environ.get(name, "") for name in REQUIRED}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"not set: {', '.join(missing)}")
    return Settings(
        submission_token=values["SUBMISSION_TOKEN"],
        github=GitHubClient(token=values["REGISTRY_TOKEN"]),
        # The field stays `github_owner`, it really is a GitHub account. Only
        # the environment variable is namespaced, to keep it collision-free.
        config=SubmissionConfig(
            github_owner=values["REGISTRY_OWNER"],
            registry_repo=values["REGISTRY_REPO"],
        ),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Read the environment before serving anything.

    A misconfigured webhook then refuses to start, where reading the variables
    per request would let it answer /health and fail on the first real
    submission, which is the moment nobody is watching. The container restarts
    in a loop until .env is complete, and `docker compose logs submission`
    names what is missing.
    """
    app.state.settings = app.state.build()
    yield


app = FastAPI(title="maDMP submission webhook", lifespan=lifespan)

# The one seam: the tests install a builder that returns a fake GitHub and a
# fixed config, so nothing in the suite depends on the process environment.
app.state.build = _from_environment


@app.get("/health")
def health() -> dict[str, str]:
    """Answers only once the four variables are in place, see lifespan."""
    return {"status": "ok"}


@app.post("/submissions")
async def submissions(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    # compare_digest rather than !=: it takes the same time whether the first
    # character is wrong or only the last, so a rejected token tells nothing
    # about the right one. On bytes, because it refuses non-ASCII strings and
    # this header is whatever the caller sent.
    presented = request.headers.get("authorization", "").encode()
    expected = f"Bearer {settings.submission_token}".encode()
    if not hmac.compare_digest(presented, expected):
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
        # DSW sends one file. A second part would be ignored rather than
        # refused: the part names belong to DSW, so refusing what it might add
        # later is worse than reading the first thing it sends.
        raw = await uploads[0].read()
    else:
        raw = await request.body()

    try:
        document = json.loads(raw)
    except ValueError as e:
        raise HTTPException(400, "body is not valid JSON") from e

    try:
        # In a thread, not on the event loop: the GitHub client blocks, and this
        # endpoint has to be async to read the body above. Awaiting the call
        # directly would freeze the process until GitHub answers, /health
        # included.
        result = await run_in_threadpool(
            handle_submission, document, folder, settings.github, settings.config
        )
    except SubmissionError as e:
        raise HTTPException(400, str(e)) from e
    # Anything GitHub refused, a write included. 502 rather than 500: the
    # webhook worked, the service behind it did not.
    except GitHubError as e:
        raise HTTPException(502, str(e)) from e
    # DSW displays the Location header as a clickable link on the submission,
    # and it is the one thing the researcher is handed. Point it at the pull
    # request carrying their DMP, at the folder when there is none to carry.
    location = result["pull_request"] or result["repository"]
    return JSONResponse(result, headers={"Location": location})
