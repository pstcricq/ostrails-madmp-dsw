"""Thin GitHub Contents API client: the two calls the webhook needs.

Deliberately synchronous and stdlib-only (the webhook serves one DSW
instance, not traffic): FastAPI runs sync endpoints in a thread pool, so
blocking I/O here is fine. Kept as its own class so tests can swap in a
fake with the same two methods.

madmp-core carries its own copy of this file (`registry/github.py`). That is
deliberate: the two repositories share the
registry *layout* — documented in dmp-registry's README — not this code.
It is a generic HTTP wrapper with no knowledge of DMPs, so the copies
cannot drift in meaning.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any


class GitHubError(RuntimeError):
    """GitHub rejected a call the webhook cannot recover from."""

    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"GitHub API error {status}: {message}")


class GitHubClient:
    def __init__(self, token: str, api_url: str = "https://api.github.com"):
        self.token = token
        self.api_url = api_url.rstrip("/")

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        request = urllib.request.Request(
            self.api_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
                return response.status, json.loads(payload) if payload else None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, None
            raise GitHubError(e.code, e.read().decode()) from e

    def get_file(self, owner: str, repo: str, path: str) -> dict[str, Any] | None:
        """The file's contents entry ({sha, content base64, ...}), or None."""
        status, data = self._request("GET", f"/repos/{owner}/{repo}/contents/{path}")
        return data if status != 404 else None

    def put_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: bytes,
        message: str,
        sha: str | None = None,
    ) -> dict[str, Any]:
        """Create or (when `sha` names the current version) update a file."""
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content).decode(),
        }
        if sha:
            body["sha"] = sha
        _, data = self._request("PUT", f"/repos/{owner}/{repo}/contents/{path}", body)
        return data
