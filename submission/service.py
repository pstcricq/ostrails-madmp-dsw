"""The webhook's logic, free of HTTP plumbing.

Stateless: everything derives from the document, the folder and a small static
config. Nothing here creates a repository or any scaffolding, the folder and
its ``meta.yaml`` are laid out beforehand from madmp-core.
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

# (!!) The registry's default branch, and part of every dmp_id this webhook
# writes. A dmp_id is the DMP's stable identifier, so moving the registry to
# another branch would leave every identifier ever issued pointing nowhere.
_BRANCH = "main"


class SubmissionError(ValueError):
    """The submission cannot be routed to a registry folder."""


@dataclass(frozen=True)
class SubmissionConfig:
    """Static webhook configuration (from the environment, see app.py)."""

    # Both required, neither defaulted: the values live in .env.example and
    # nowhere else, and app.py refuses to build this without them.
    github_owner: str  # account owning the dmp-registry repo
    registry_repo: str  # the mono-repo all projects live in


def handle_submission(
    document: Any, folder: str, github: GitHubClient, config: SubmissionConfig
) -> dict[str, Any]:
    """Commit the DMP into ``projects/<folder>/template/`` of the registry and
    rewrite ``dmp_id``, in the document it is given, to that file's raw URL.
    Idempotent: an unchanged DMP commits nothing. Returns a small summary DSW
    shows as the result."""
    if not _FOLDER_RE.match(folder or ""):
        raise SubmissionError(f"invalid project folder {folder!r}")
    if not isinstance(document, dict) or not isinstance(document.get("dmp"), dict):
        raise SubmissionError("document has no dmp object")

    owner, repo = config.github_owner, config.registry_repo
    base = f"projects/{folder}"
    # The folder must have been laid out from madmp-core, otherwise there is
    # no meta.yaml (no rules pins) and dropping a DMP would leave an orphan.
    # Refuse rather than create a half-folder.
    if github.get_file(owner, repo, f"{base}/meta.yaml") is None:
        raise SubmissionError(
            f"{base}/ is not initialized in {repo}, or not visible with this "
            f"token. Register the project from madmp-core first."
        )

    file_path = f"{base}/template/dmp_{folder}_template.json"
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{_BRANCH}/{file_path}"
    # The template set dmp_id to the DSW project URL as a placeholder, the
    # registry location is the DMP's real identifier.
    document["dmp"]["dmp_id"] = {"identifier": raw_url, "type": "url"}

    content = (
        json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    ).encode()

    existing = github.get_file(owner, repo, file_path)
    if existing is not None:
        # (!!) GitHub inlines the content up to 1 MB only, and answers with an
        # empty `content` and `encoding: "none"` above that. A DMP that large
        # would never compare equal, so it would be committed again on every
        # submission instead of reported unchanged.
        current = base64.b64decode(existing.get("content") or "")
        if current == content:
            action = "unchanged"
        else:
            github.put_file(
                owner,
                repo,
                file_path,
                content,
                f"Update DMP for {folder} (DSW submission)",
                sha=existing["sha"],
            )
            action = "updated"
    else:
        # Named after the folder: every project commits into the same repo, and
        # `git log` shows the message before the path.
        message = f"Add DMP for {folder} (DSW submission)"
        github.put_file(owner, repo, file_path, content, message)
        action = "created"

    return {
        "repository": f"https://github.com/{owner}/{repo}/tree/{_BRANCH}/{base}",
        "file": file_path,
        "action": action,
        "folder": folder,
    }
