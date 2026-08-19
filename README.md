# madmp-dsw

A Data Stewardship Wizard 4.31 deployment for the maDMP project, plus one
service that is not DSW's: the **submission webhook**, which commits a rendered
DMP into the registry repository. It carries no code of its own. The webhook is
built and published by
[madmp-core](https://github.com/pstcricq/ostrails-madmp-core), and what is here
is the image tag, the four variables and the compose service that runs it.

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

Twenty values, in two groups:

- **twelve carry a working value**: the five image versions, the database name,
  the two usernames, the bucket, and the three URLs of a local deployment
- **eight are empty**: the two passwords, the two DSW signing secrets, and the
  webhook's four variables. `.env.example` says what each one is and gives the
  command that produces it where there is one

The five image versions include `MADMP_CORE_VERSION`, which is not a
dependency's version but the webhook's, and the one value here that decides
which engine and which rules a submitted DMP is judged by.

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

**Neither the webhook nor its image is in this repository.** Both live in
[madmp-core](https://github.com/pstcricq/ostrails-madmp-core), next to the
rules the webhook checks a document against, and that repository's CI publishes
the image on every version tag. What is here is the deployment: the compose
service, its four variables, and the tag it pulls.

DSW's Submit feature POSTs a rendered DMP to
`http://submission:8080/submissions?project=<folder>` on the compose network, so
the service needs no published port.

**The document is checked before it is committed.** It carries a `metadata`
object beside `dmp`, naming the rules versions it was built from, and the
webhook merges those rules and runs madmp-core's engine over it. A document
that does not hold up is refused with a `422` naming the first few violations,
and never reaches the registry, so the researcher reads why while still in
DSW.

What passes is committed as **three files in one commit**: the DMP as RDA DCS
alone, that `metadata` object beside it, and the verdict it got. The `dmp_id`,
a DSW placeholder until then, is rewritten to the DMP's stable raw URL.

It creates nothing. A folder with no `template/.gitkeep` is refused rather than
half built, the folder being laid out beforehand from madmp-core's CI.

Submitting the same DMP twice commits nothing the second time.

The token it commits with needs `Contents: Read and write` on the registry
repository, and nothing else.

Nothing is built here. The image comes from
`ghcr.io/pstcricq/ostrails-madmp-core/submission`, at the tag
`MADMP_CORE_VERSION` names in `.env`. Bumping that one value is how a
deployment moves to a newer engine or newer rules, and it is the only thing
here that decides which madmp-core is in use:

```bash
docker compose pull submission && docker compose up -d submission
```

The package is private for as long as madmp-core is, so the host has to be
logged in to pull it. Once, with a token carrying `read:packages`:

```bash
docker login ghcr.io -u <github-user>
```

Without it the pull fails with a 401 that reads like the image does not exist.

The container needs **outbound HTTPS to `api.github.com`**, which is the one
thing in this deployment that reaches outside the host. Everything else talks
on the compose network.

Wiring it up in DSW means declaring a submission service with two things: the
URL above, carrying the project's folder in its query string, and one static
header, `Authorization: Bearer <SUBMISSION_TOKEN>`. madmp-core's `dsw.publish
submission` writes that configuration through the API.

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

Not here. Its code, its tests and its image are in madmp-core, and it is
checked where it lives. What reaches this repository is a tag in `.env`.

CI here runs the three checks this repository can answer without deploying:
that the compose file resolves against `.env.example`, that `scripts/setup.sh`
passes shellcheck, and that the workflows pass actionlint. Whether a deployment
is *correct* only shows on a real host, and nothing here pretends otherwise.

## Layout

```
docker-compose.yml          the seven services
.env.example                every value the stack reads
scripts/setup.sh            reports the configuration, then brings the stack up
.github/workflows/ci.yml    three checks on the deployment files
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
