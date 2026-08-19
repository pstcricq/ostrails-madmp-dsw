# The submission webhook, installed from madmp-core rather than copied from
# here. The check it runs on a document needs that repository's engine and its
# rules files, and pinning one version of it pins both: a document is checked
# by the release this image names, and every verdict it commits says so.
#
# Two stages, so neither git nor the token that reads a private repository is
# anywhere in what runs.

# Bumped when a deployment is to run a newer engine or newer rules. Nothing
# else in this repository decides which madmp-core is in use.
ARG MADMP_CORE_VERSION=v1.0.0

# Floating on purpose, where the compose file pins every other image to a patch
# release: this tag carries Debian's security updates.
FROM python:3.12-slim AS build
ARG MADMP_CORE_VERSION

# git, because pip installs from a tag of a repository. It stays in this stage.
RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/*

# A BuildKit secret, never a build argument: an argument is readable in the
# image history for anyone who pulls it, and this one reads a private
# repository.
RUN --mount=type=secret,id=madmp_core_token \
    python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir \
    "madmp-core[submission] @ git+https://x-access-token:$(cat /run/secrets/madmp_core_token)@github.com/pstcricq/ostrails-madmp-core@${MADMP_CORE_VERSION}"

FROM python:3.12-slim
COPY --from=build /venv /venv

# The webhook reads a request and writes to GitHub, so it needs no privileges
# on this machine at all. Root inside a container is root on the host kernel
# for anything that escapes it.
RUN useradd --system --no-create-home webhook
USER webhook

EXPOSE 8080
CMD ["/venv/bin/uvicorn", "submission.app:app", "--host", "0.0.0.0", "--port", "8080"]
