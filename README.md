# madmp-dsw

A Data Stewardship Wizard 4.31 deployment for the maDMP project, plus the one
piece of code it carries of its own: the **submission webhook**, which commits a
rendered DMP into the `dmp-registry` repository.

It starts from the [official deployment
example](https://github.com/ds-wizard/dsw-deployment-example) at its 4.31
release, kept as the `upstream` remote. What differs from it is listed at the
end of this file, and why it differs is in [TECHNICAL_GUIDE.md](TECHNICAL_GUIDE.md).

## Quick start

```bash
cp .env.example .env
```

Fill in the eight empty values. Each one has, right above it, what it is and how
to produce it. Then:

```bash
bash scripts/setup.sh
```

The script prints what the stack will run with, starts it, creates the bucket
and tells you where to go. It writes nothing and can be re-run at will. The two
commands it wraps are:

```bash
docker compose up -d --wait && docker compose run --rm createbucket
```

Measured on a laptop, from no image and no volume: about a minute of downloads,
then twelve seconds. On a machine that already has the images, twelve seconds.

| | |
|---|---|
| Client | http://localhost:8080/wizard |
| API | http://localhost:3000/wizard-api |
| MinIO console | http://localhost:9001 |

**Enter through `/wizard`, not through the bare origin.** The client image
redirects `/` to an absolute `http://` address built from the protocol nginx
itself listens on, so behind a TLS terminator the browser is sent to `http` on a
host that only serves `https`. Behind your own reverse proxy the fix belongs
there, with `proxy_redirect http:// https://` or a root redirect of its own.

## What runs

| Service | Port on the host | Role |
|---|---|---|
| `client` | 127.0.0.1:8080 | the web interface |
| `server` | 127.0.0.1:3000 | the API |
| `docworker` | none | renders documents |
| `submission` | none | the maDMP webhook, reached by DSW as `submission:8080` |
| `postgres` | none | the database |
| `minio` | 127.0.0.1:9000, 9001 | object storage, and its console |
| `createbucket` | one-shot | creates the bucket, under the `tools` profile |

Everything is bound to `127.0.0.1`. Reaching the stack from another machine
means putting a reverse proxy in front of it, see below.

`postgres`, `minio`, `server` and `submission` have healthchecks, which is what
makes `up --wait` mean something: it returns when the stack answers, not when
the containers exist.

## Configuration

**`.env` is the single source of truth, and it is filled by hand.** There is no
`application.yml` and no script that generates anything.

DSW resolves every setting from the environment before reading its config file,
so `docker-compose.yml` composes DSW's own settings out of the values in `.env`.
The variable names are the `application.yml` paths in upper case:
`general.clientUrl` is `GENERAL_CLIENT_URL`, `s3.url` is `S3_URL`.

Nineteen values, in two groups:

- **eleven carry a working value**: the four image versions, the database name,
  the two usernames, the bucket, and the three URLs of a local deployment
- **eight are empty**: the two passwords, the two DSW signing secrets, and the
  webhook's four variables. `.env.example` says what each one is and gives the
  command that produces it where there is one

`scripts/setup.sh` reports every value it finds, where it comes from, and stops
if one is missing. Secrets are reported as `set`, never printed.

Compose lets the environment win over `.env`. A name exported in the shell, or
provided by whatever runs the containers, therefore reaches them while `.env`
still shows something else. That is why the script reports the origin of each
value rather than reading the file alone.

## Deploying somewhere else

Three values change, and they are the ones the **browser** resolves, so they
depend on where the stack is reached from, not on where it runs:

```
API_URL=https://dsw.example.org/wizard-api
CLIENT_URL=https://dsw.example.org/wizard
S3_URL=...
```

The `/wizard` and `/wizard-api` paths are what lets both live under one domain,
with a reverse proxy routing the first to port 8080 and the second to port 3000.

`S3_URL` is the awkward one. The browser downloads documents straight from
MinIO through a presigned URL, so MinIO needs a public address of its own. A
subdomain is simpler than a path: MinIO puts the bucket name in the path, so
proxying it under `/s3` means rewriting URLs.

Two cases need a change in `docker-compose.yml` itself rather than in `.env`:

- the reverse proxy runs on **another machine**, so the `127.0.0.1:` prefixes
  have to go, with a firewall taking over
- the infrastructure provides a **managed Postgres or S3**, which means removing
  services rather than changing values

## The submission webhook

DSW's Submit feature POSTs a rendered DMP to
`http://submission:8080/submissions?project=<folder>` on the compose network, so
the service needs no published port. The webhook commits the document into
`projects/<folder>/template/` of the registry repository and rewrites its
`dmp_id`, a DSW placeholder until then, to that file's stable raw URL.

It creates nothing. A folder with no `meta.yaml` is refused rather than half
built: that file carries the identity and the rules pins the quality checks
read, and it is laid out beforehand from madmp-core.

Submitting the same DMP twice commits nothing the second time.

Wiring it up in DSW means declaring a submission service with two things: the
URL above, carrying the project's folder in its query string, and one static
header, `Authorization: Bearer <SUBMISSION_TOKEN>`. madmp-core's `dsw.publish
submission` writes that configuration through the API.

What it answers:

| | |
|---|---|
| 200 | with `action` being `created`, `updated` or `unchanged`, and a `Location` header DSW shows as a link |
| 400 | the folder is missing, unsafe, or not laid out, or the body is not a DMP |
| 401 | wrong or missing token |
| 502 | GitHub refused the call, or could not be reached |

Four variables, and they are the whole contract:

| | |
|---|---|
| `SUBMISSION_TOKEN` | the shared secret above |
| `REGISTRY_TOKEN` | fine-grained PAT, Contents RW on the registry repo |
| `REGISTRY_OWNER`, `REGISTRY_REPO` | where the registry lives |

They are read once, at startup. An incomplete `.env` leaves the container
restarting in a loop, and `docker compose logs submission` names what is
missing, rather than the webhook answering `/health` and failing on the first
real submission.

## Accounts

DSW seeds three demo accounts whose addresses and password are published, and
`setup.sh` says so at the end of every run for as long as they answer. Removing
them is a manual step, in this order:

1. log in as `albert.einstein@example.com` / `password`
2. create your own administrator account
3. log in as yourself, check that it works
4. delete the three seeded accounts

The order matters: deleting them first locks you out of your own instance with
no way back but `psql`.

`system@example.com` is not one of them. It is flagged `machine`, carries no
usable password hash, and DSW uses it internally.

## Everyday commands

```bash
docker compose ps                      # what is running, and whether it is healthy
docker compose logs -f server          # the server, including the config it applied
docker compose logs submission         # what the webhook did with a submission
docker compose exec postgres psql -U postgres -d engine-wizard
docker compose run --rm createbucket   # idempotent, safe to repeat
docker compose stop                    # keeps containers and data
docker compose down                    # removes containers, keeps the volumes
docker compose down -v                 # removes the data too
```

Inspecting the database needs no published port, `exec` goes through the Docker
daemon rather than the network.

## Working on the webhook

```bash
uv run pytest -q          # 39 tests
uv run ruff check .
uv run ruff format .
```

CI runs those, plus three checks on the deployment itself: that
`submission/requirements.txt` still matches `uv.lock`, that the compose file
resolves, and that `scripts/setup.sh` passes shellcheck.

Adding a dependency means regenerating the file the image installs from:

```bash
uv export -q --frozen --no-dev --no-emit-project -o submission/requirements.txt
```

## Layout

```
docker-compose.yml          the seven services
.env.example                every value the stack reads
scripts/setup.sh            reports the configuration, then brings the stack up
submission/                 the webhook: app.py, service.py, github_client.py
  Dockerfile                its image, installing from requirements.txt
  requirements.txt          generated from uv.lock, hashes included
tests/                      the webhook's unit tests
pyproject.toml, uv.lock     one uv environment for the webhook and its tests
.github/workflows/ci.yml    six checks
```

## What differs from upstream

- configuration comes from `.env` through the environment, and
  `config/application.yml` is gone along with its published signing secrets
- the compose project name is pinned, so container and volume names survive a
  folder rename
- named volumes are enabled. Without them `docker compose down` destroys the
  database and the bucket
- healthchecks on Postgres and on the server, the second replacing one that
  could not report healthy in under five minutes
- Postgres is not published, and MinIO is bound to the loopback
- the bucket is created by a compose service under a profile, replacing a script
  that guessed its network and asked for an `mc` image tag that does not exist
- the mailer is commented out, having nothing to process while mail is disabled
- upstream's `.github` is removed, those workflows monitor DSW's own images

## License

MIT, from the upstream deployment example. See [LICENSE](LICENSE).
