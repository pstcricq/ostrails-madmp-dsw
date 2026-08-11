# Standalone image for the submission webhook: these three modules and their
# dependencies, nothing else. Built by docker compose with `build: ./submission`,
# so the build context is this folder and nothing above it is reachable.

# Floating on purpose, where the compose file pins every other image to a patch
# release: this tag carries Debian's security updates, and the versions that
# matter are pinned below.
FROM python:3.12-slim

# Generated from uv.lock, hashes included, and CI fails if the two drift:
#
#   uv export --frozen --no-dev --no-emit-project -o submission/requirements.txt
#
# --require-hashes is what makes it worth having. Without it the file pins
# versions, with it the build refuses anything whose content is not the one the
# lock resolved.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

WORKDIR /app
COPY *.py /app/

# The webhook reads a request and writes nothing, so it needs no privileges at
# all. Root inside a container is root on the host kernel for anything that
# escapes it.
RUN useradd --system --no-create-home webhook
USER webhook

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
