# madmp-dsw

A Data Stewardship Wizard 4.31 deployment for the maDMP project, plus one
service that is not DSW's: the **submission webhook**, which commits a rendered
DMP into the registry repository.

This repository carries no application code. The webhook is built and published
by [madmp-core](https://github.com/pstcricq/ostrails-madmp-core), and what is
here is the image tag, the four variables and the compose service that runs it.
The plans it commits land in
[madmp-registry](https://github.com/pstcricq/ostrails-madmp-registry).

It starts from the [official deployment
example](https://github.com/ds-wizard/dsw-deployment-example) at its 4.31
release, kept as the `upstream` remote. What differs from it is listed at the
end of this file.

**Service by service, variable by variable, and what to look at when something
is wrong, is in the technical reference:**
<https://pstcricq.github.io/ostrails-madmp-technical-docs/dsw/01-stack/>

This README says how to run it.

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
redirects `/` to an absolute `http://` address, so behind a TLS terminator the
browser is sent to `http` on a host that only serves `https`. Behind your own
reverse proxy, fix it there with `proxy_redirect http:// https://` or a root
redirect.

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

`docker-compose.yml` composes DSW's own settings out of the values in `.env`.
The variable names are the `application.yml` paths in upper case:
`general.clientUrl` is `GENERAL_CLIENT_URL`, `s3.url` is `S3_URL`.

Twenty values, in five blocks, one per thing configured, each opening on the
version of the image that runs it:

| block | values |
|---|---|
| Data Stewardship Wizard | `DSW_VERSION`, and the two signing secrets |
| Database | `POSTGRES_VERSION`, the name, the account, the password |
| Object storage | `MINIO_VERSION`, `MC_VERSION`, the account, the bucket |
| Submission webhook | `MADMP_CORE_VERSION`, and the webhook's four variables |
| The three URLs | `API_URL`, `CLIENT_URL`, `S3_URL` |

Twelve carry a working value, eight are empty. `.env.example` says what each one
is and gives the command that produces it where there is one.

`scripts/setup.sh` reports every value it finds, where it comes from, and stops
if one is missing. Secrets are reported as `set`, never printed. It reports the
value **compose will use**: a name exported in the shell reaches the containers
while `.env` still shows something else.

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
`S3_URL` needs a public address of its own, and a subdomain is simpler than a
path.

Two cases need a change in `docker-compose.yml` itself rather than in `.env`:

- the reverse proxy runs on **another machine**, so the `127.0.0.1:` prefixes
  have to go, with a firewall taking over
- the infrastructure provides a **managed Postgres or S3**, which means removing
  services rather than changing values

## The submission webhook

DSW's Submit feature POSTs a rendered DMP to
`http://submission:8080/submissions?project=<folder>` on the compose network, so
the service needs no published port.

The document is checked against the rules it names before it is committed. What
passes is committed as three files in one commit: the DMP as RDA DCS alone, the
`metadata` object naming the versions it was built from, and the verdict it got.
What does not is refused with a `422` and never reaches the registry.

What it answers:

| | |
|---|---|
| 200 | with `action` being `created`, `updated` or `unchanged`, and a `Location` header DSW shows as a link to the folder |
| 400 | the folder is missing, unsafe, or not laid out, or the body is not a DMP carrying its `metadata` object |
| 422 | the DMP does not hold up against the rules it names |
| 401 | wrong or missing token |
| 502 | GitHub refused the call, or could not be reached |

Four variables, and they are the whole contract:

| | |
|---|---|
| `SUBMISSION_TOKEN` | the shared secret DSW sends as `Authorization: Bearer ...` |
| `REGISTRY_TOKEN` | fine-grained PAT, Contents RW on the registry repo |
| `REGISTRY_OWNER`, `REGISTRY_REPO` | where the registry lives |

They are read once, at startup. An incomplete `.env` leaves the container
restarting in a loop, and `docker compose logs submission` names what is
missing.

The container needs **outbound HTTPS to `api.github.com`**.

### Its image

Nothing is built here. The image comes from
`ghcr.io/pstcricq/ostrails-madmp-core/submission`, at the tag
`MADMP_CORE_VERSION` names in `.env`. Bumping that one value is how a deployment
moves to a newer engine or newer rules:

```bash
docker compose pull submission && docker compose up -d submission
```

The package is private for as long as madmp-core is, so the host has to be
logged in to pull it. Once, with a token carrying `read:packages`:

```bash
docker login ghcr.io -u <github-user>
```

Without it the pull fails with a 401 that reads like the image does not exist.

### Wiring it up in DSW

A submission service is declared with two things: the URL above, carrying the
project's folder in its query string, and one static header,
`Authorization: Bearer <SUBMISSION_TOKEN>`. madmp-core writes that configuration
through the DSW API.

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

## Layout

```
docker-compose.yml          the seven services
.env.example                every value the stack reads
scripts/setup.sh            reports the configuration, then brings the stack up
.github/workflows/ci.yml    three checks on the deployment files
```

CI runs `docker compose config` against `.env.example`, shellcheck on the setup
script, and actionlint on the workflows. Nothing is built, pulled or deployed
there.

## What differs from upstream

- configuration comes from `.env` through the environment, and
  `config/application.yml` is gone along with its published signing secrets
- the compose project name is pinned, so container and volume names survive a
  folder rename
- named volumes are enabled
- healthchecks on Postgres and on the server, the second replacing the image's
  own
- `platform: linux/amd64` on the server, which is published for amd64 only
- Postgres is not published, and MinIO is bound to the loopback
- the bucket is created by a compose service under a profile, replacing a script
- the mailer is commented out
- upstream's `.github` is removed, those workflows monitor DSW's own images
- the submission service is added, which upstream has no equivalent of

## License

Two, because this repository is a derivative.

The deployment example it starts from is **MIT**, Copyright (c) 2019 Data
Stewardship Wizard, and that notice stays where it is: [LICENSE](LICENSE).

Everything written for this repository is **Apache-2.0**, Copyright 2026
Pierre St-Cricq dit Lompre (SOCIB): [LICENSE-APACHE](LICENSE-APACHE). That
covers the compose file, `scripts/setup.sh`, `.env.example` and the
documentation here.
