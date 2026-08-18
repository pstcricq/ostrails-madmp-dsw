"""The webhook's logic, free of HTTP plumbing.

A submission is offered, not merged. It lands on a branch of its own and a
pull request carries it, so the registry's default branch only ever holds
documents a check has passed.

Stateless: everything derives from the document, the folder and a small static
config. Nothing here creates a repository or any scaffolding, the folder and
its subdirectories are laid out beforehand from madmp-core.
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
#
# A submitted DMP is not there yet, it waits on the branch below for its pull
# request. The identifier is a promise, kept when that is merged.
_BRANCH = "main"

# One branch per project, not per submission. A researcher who submits five
# times has one place to look, and the fifth replaces the fourth instead of
# opening a fifth pull request nobody closes.
_BRANCH_PREFIX = "submission/"

# The key the document template renders beside `dmp`, carrying the versions
# the document was built from.
_ENVELOPE = "metadata"


class SubmissionError(ValueError):
    """The submission cannot be routed to a registry folder."""


@dataclass(frozen=True)
class SubmissionConfig:
    """Static webhook configuration (from the environment, see app.py)."""

    # Both required, neither defaulted: the values live in .env.example and
    # nowhere else, and app.py refuses to build this without them.
    github_owner: str  # account owning the dmp-registry repo
    registry_repo: str  # the mono-repo all projects live in


def _pins_are_well_formed(rules: Any) -> bool:
    """Whether `rules` is a non-empty list of one-key mappings of strings,
    which is the shape a pinned rules version is written in."""
    return (
        isinstance(rules, list)
        and bool(rules)
        and all(
            isinstance(pin, dict)
            and len(pin) == 1
            and all(
                isinstance(part, str) and part for part in (*pin.keys(), *pin.values())
            )
            for pin in rules
        )
    )


def take_envelope(document: dict, folder: str) -> dict[str, Any]:
    """The provenance block, taken out of the document before anything is
    written, so what is committed is the `dmp` object alone.

    Absent or malformed is a refusal, not a default: a DMP whose rules
    versions are unknown cannot be checked against them, and guessing is
    worse than saying so.
    """
    envelope = document.pop(_ENVELOPE, None)
    if not isinstance(envelope, dict):
        raise SubmissionError(
            f"document carries no {_ENVELOPE!r} object, so the rules versions "
            f"it was built from are unknown. It was not rendered by a maDMP "
            f"document template."
        )
    # The folder comes from the submission service's URL and the project name
    # from the template that rendered the document. Comparing them is what
    # catches one project's document submitted through another's service.
    if envelope.get("project") != folder:
        raise SubmissionError(
            f"document was generated for project {envelope.get('project')!r}, "
            f"submitted to {folder!r}"
        )
    if (
        not isinstance(envelope.get("template_version"), str)
        or not envelope["template_version"]
    ):
        raise SubmissionError(f"{_ENVELOPE}.template_version is missing or empty")
    if not _pins_are_well_formed(envelope.get("rules")):
        raise SubmissionError(
            f"{_ENVELOPE}.rules is not a non-empty list of {{standard: version}} "
            f"mappings"
        )
    return envelope


def _bytes(document: Any) -> bytes:
    """One JSON file as it is committed. Trailing newline, so the registry
    holds text files git and every editor agree on."""
    return (
        json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    ).encode()


def _stored(entry: dict | None) -> bytes | None:
    """What the registry currently holds at a path, or None when nothing.

    (!!) GitHub inlines the content up to 1 MB only, and answers with an empty
    `content` and `encoding: "none"` above that. A DMP that large would never
    compare equal, so it would be committed again on every submission instead
    of reported unchanged.
    """
    if entry is None:
        return None
    return base64.b64decode(entry.get("content") or "")


def _pull_request_body(dmp_path: str, meta_path: str) -> str:
    """What the pull request says to whoever opens it."""
    return (
        "Submitted from DSW by the maDMP submission webhook.\n"
        "\n"
        f"- `{dmp_path}`, the DMP, RDA DCS and nothing else\n"
        f"- `{meta_path}`, the rules versions it was built from\n"
        "\n"
        "Quality control checks the DMP against those versions. A red check "
        "means the document is to be fixed in DSW and submitted again, which "
        "updates this pull request rather than opening another.\n"
    )


def handle_submission(
    document: Any, folder: str, github: GitHubClient, config: SubmissionConfig
) -> dict[str, Any]:
    """Offer the DMP and its provenance for ``projects/<folder>/template/`` of
    the registry, on a branch of their own and under one pull request per
    project, and rewrite ``dmp_id``, in the document it is given, to the DMP's
    raw URL on the default branch.

    The two files go in one commit, so nothing ever holds a DMP whose versions
    are missing. Idempotent: a submission that says what is already offered
    commits nothing. Returns a small summary DSW shows as the result."""
    if not _FOLDER_RE.match(folder or ""):
        raise SubmissionError(f"invalid project folder {folder!r}")
    if not isinstance(document, dict) or not isinstance(document.get("dmp"), dict):
        raise SubmissionError("document has no dmp object")
    envelope = take_envelope(document, folder)

    owner, repo = config.github_owner, config.registry_repo
    base = f"projects/{folder}"
    # The folder must have been laid out from madmp-core, otherwise dropping a
    # DMP would leave it in a folder nobody registered. Refuse rather than
    # create a half-folder.
    if github.get_file(owner, repo, f"{base}/template/.gitkeep") is None:
        raise SubmissionError(
            f"{base}/ is not initialized in {repo}, or not visible with this "
            f"token. Register the project from madmp-core first."
        )

    dmp_path = f"{base}/template/dmp_{folder}_template.json"
    meta_path = f"{base}/template/dmp_{folder}_template.meta.json"
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{_BRANCH}/{dmp_path}"
    # The template set dmp_id to the DSW project URL as a placeholder, the
    # registry location is the DMP's real identifier.
    document["dmp"]["dmp_id"] = {"identifier": raw_url, "type": "url"}

    wanted = {dmp_path: _bytes(document), meta_path: _bytes(envelope)}

    # What this submission builds on, and what it is compared with. An open
    # pull request means the branch holds a submission under review, so the
    # next one continues it. Without one, the branch is either absent or left
    # over from a review already merged, and the submission starts again from
    # the default branch.
    branch = f"{_BRANCH_PREFIX}{folder}"
    pull = github.open_pull_request_for(owner, repo, branch)
    head = github.branch_head(owner, repo, branch) if pull else None
    parent = head or github.branch_head(owner, repo, _BRANCH)
    if parent is None:
        raise SubmissionError(f"{repo} has no {_BRANCH} branch to offer against")

    read_ref = branch if head else _BRANCH
    current = {
        path: _stored(github.get_file(owner, repo, path, ref=read_ref))
        for path in wanted
    }

    if current == wanted:
        action = "unchanged"
    else:
        action = "created" if current[dmp_path] is None else "updated"
        verb = "Add" if action == "created" else "Update"
        # Named after the folder: every project is offered through the same
        # repository, and `git log` shows the message before the path.
        github.commit_files(
            owner,
            repo,
            branch,
            wanted,
            f"{verb} DMP for {folder} (DSW submission)",
            parent,
        )
        pull = github.open_pull_request(
            owner,
            repo,
            branch,
            _BRANCH,
            f"Submit DMP for {folder}",
            _pull_request_body(dmp_path, meta_path),
        )

    return {
        "repository": f"https://github.com/{owner}/{repo}/tree/{_BRANCH}/{base}",
        "pull_request": pull["html_url"] if pull else None,
        "branch": branch,
        "file": dmp_path,
        "metadata": meta_path,
        "action": action,
        "folder": folder,
    }
