"""The webhook's actual logic, free of HTTP plumbing.

The submission service (this webhook) is fixed: DSW POSTs a rendered DMP to
``/submissions?project=<folder>``. The per-project folder — laid out ahead of
time from madmp-core's ``registry/``, and named after the project's ``id`` —
is the only routing input. The webhook drops the DMP into that folder's ``template/`` and
rewrites ``dmp_id`` (a DSW placeholder at this point) to the DMP's stable raw
URL in the registry. It creates no repos and no scaffolding: the folder and its
``meta.yaml`` (identity + rules pins the QC reads) already exist.

Stateless: everything derives from the document, the folder, and a small
static config.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

from github_client import GitHubClient

# A safe folder slug: no dots or slashes, so a submission can never escape
# projects/<folder>/ (path traversal) or name anything but a folder.
_FOLDER_RE = re.compile(r"^[a-z0-9-]{1,64}$")


class SubmissionError(ValueError):
    """The submission cannot be routed to a registry folder."""


@dataclass(frozen=True)
class SubmissionConfig:
    """Static webhook configuration (from the environment, see app.py)."""

    github_owner: str  # account owning the dmp-registry repo
    registry_repo: str = "dmp-registry"  # the mono-repo all projects live in


def handle_submission(
    document: Any, folder: str, github: GitHubClient, config: SubmissionConfig
) -> dict[str, Any]:
    """Commit the DMP into ``projects/<folder>/template/`` of the registry and
    rewrite its ``dmp_id`` to that file's raw URL. Idempotent: an unchanged DMP
    commits nothing. Returns a small summary DSW shows as the result."""
    if not _FOLDER_RE.match(folder or ""):
        raise SubmissionError(f"invalid project folder {folder!r}")
    if not isinstance(document, dict) or not isinstance(document.get("dmp"), dict):
        raise SubmissionError("document has no dmp object")

    owner, repo = config.github_owner, config.registry_repo
    base = f"projects/{folder}"
    # The folder must have been laid out from madmp-core — otherwise there is
    # no meta.yaml (no rules pins) and dropping a DMP would leave an orphan.
    # Refuse rather than create a half-folder.
    if github.get_file(owner, repo, f"{base}/meta.yaml") is None:
        raise SubmissionError(
            f"{base}/ is not initialized in {repo} — register the project from "
            f"madmp-core first"
        )

    file_path = f"{base}/template/dmp_{folder}_template.json"
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/{file_path}"
    # The template set dmp_id to the DSW project URL as a placeholder; the
    # registry location is the DMP's real identifier.
    document["dmp"]["dmp_id"] = {"identifier": raw_url, "type": "url"}

    content = (
        json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    ).encode()

    existing = github.get_file(owner, repo, file_path)
    if existing is not None:
        current = base64.b64decode(existing.get("content") or "")
        if current == content:
            action = "unchanged"
        else:
            github.put_file(
                owner,
                repo,
                file_path,
                content,
                "Update DMP (DSW submission)",
                sha=existing["sha"],
            )
            action = "updated"
    else:
        github.put_file(owner, repo, file_path, content, "Add DMP (DSW submission)")
        action = "created"

    return {
        "repository": f"https://github.com/{owner}/{repo}/tree/main/{base}",
        "file": file_path,
        "action": action,
        "folder": folder,
    }
