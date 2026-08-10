# Standalone image for the submission webhook: it only needs FastAPI and
# the stdlib — no rules/QC machinery, nothing from madmp-core — so the
# build copies just these few modules. Built by docker compose with
# `build: ./submission` (context = this folder).
FROM python:3.12-slim

RUN pip install --no-cache-dir fastapi uvicorn python-multipart

WORKDIR /app
COPY *.py /app/

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
