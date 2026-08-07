# madmp-dsw

A Data Stewardship Wizard 4.31 deployment for the maDMP project, meant to run
online in a Codespace so that content published from CI can be tested and looked
at in a browser.

It starts from the [official deployment
example](https://github.com/ds-wizard/dsw-deployment-example) at its 4.31
release, kept as the `upstream` remote, and is reduced to seven files.

## Quick start

```bash
bash scripts/setup.sh
```

That is the whole procedure, locally and in a Codespace alike. It creates
`.env`, generates the secrets, starts the stack, creates the bucket, and prints
where to go. Running it again is safe.

In a Codespace it runs by itself at creation, and the three URLs point at the
forwarded domain instead of localhost.

| | Local |
|---|---|
| Client | http://localhost:8080/wizard |
| API | http://localhost:3000/wizard-api |
| MinIO console | http://localhost:9001 |

## What is in here

| File | Role |
|---|---|
| `docker-compose.yml` | the five services, plus a one-shot bucket creator |
| `.env.example` | every value the stack reads, with its defaults documented |
| `scripts/setup.sh` | from nothing to a running stack, in one command |
| `.devcontainer/devcontainer.json` | the Codespace: ports, visibility, lifecycle |

## Configuration

**`.env` is the single source of truth.** There is no `application.yml`.

DSW resolves every setting from the environment before reading its config file,
so `docker-compose.yml` composes DSW's own settings out of the values in `.env`.
The Postgres password, for instance, is written once and turned into a
connection string there. Nothing is spelled out twice, so nothing can drift.

The environment variable names are the `application.yml` paths in upper case:
`general.clientUrl` is `GENERAL_CLIENT_URL`, `s3.url` is `S3_URL`. The server
logs each one it applies, so `docker compose logs server` shows what arrived.

Values fall into four groups, and `.env.example` says which is which:

- **fixed**, copied as they are: the four versions, the two usernames, the
  bucket name
- **computed** by `setup.sh` depending on where it runs: the three URLs, which
  are the only values that depend on the machine, because they are what the
  *browser* resolves
- **generated** by `setup.sh`, once, and never regenerated: the two
  infrastructure passwords and the two DSW signing secrets. Every empty value in
  `.env.example` is one of these
- **secrets**, read from the environment and never stored here:
  `DSW_ADMIN_EMAIL` and `DSW_ADMIN_PASSWORD`

To read a generated value back:

```bash
grep MINIO_ROOT_PASSWORD .env
```

## Ports

| Port | Service | Codespace visibility |
|---|---|---|
| 8080 | client | private |
| 3000 | server API | **public** |
| 9000 | MinIO S3 API | **public** |
| 9001 | MinIO console | not forwarded |
| 5432 | Postgres | not published at all |

The split is not a preference. A private port works for a page you navigate to,
because the browser sends its GitHub cookie on a first-party navigation. That
cookie is not sent cross-origin, and the other two are read cross-origin: the
client calls the API by XHR, and the browser fetches documents straight from
MinIO through a presigned URL. Leaving those private breaks the application in
ways that look like bugs.

Port 3000 being public is also what lets a GitHub Actions runner reach the API.

Everything is bound to `127.0.0.1` on the host. Publishing on `0.0.0.0`, which
upstream does for MinIO, exposes the service to the whole network the machine
sits on, and on Linux it bypasses `ufw` entirely.

## Accounts

DSW seeds three demo accounts whose addresses and password are published.
`setup.sh` replaces them with one of your own, taken from `DSW_ADMIN_EMAIL` and
`DSW_ADMIN_PASSWORD`, and only after proving that account can log in. Without
those two variables it changes nothing and says so.

`system@example.com` is left alone. It is flagged `machine` and carries no usable
password hash, so no input opens it, and DSW uses it internally.

Since the demo account is gone, publishing from CI needs `DSW_EMAIL` and
`DSW_PASSWORD` rather than relying on the defaults in `dsw/publish.py`.

## Everyday commands

```bash
docker compose ps                      # what is running, and on which ports
docker compose logs -f server          # the server, including the config it applied
docker compose exec postgres psql -U postgres -d engine-wizard
docker compose run --rm createbucket   # idempotent, safe to repeat
docker compose stop                    # keeps containers and data
docker compose down                    # removes containers, keeps the volumes
docker compose down -v                 # removes the data too
```

Inspecting the database needs no published port: `exec` goes through the Docker
daemon rather than the network.

## Starting over

```bash
docker compose down -v && rm .env && bash scripts/setup.sh
```

Both halves are needed. Deleting `.env` alone would generate a new Postgres
password while the volume still holds an account created with the old one, and
that account is created only once, at the first `initdb`.

## What differs from upstream

- configuration comes from `.env` through the environment, and
  `config/application.yml` is gone along with its published signing secrets
- the compose project name is pinned, so container and volume names survive a
  folder rename
- named volumes are enabled. Without them `docker compose down` destroys the
  database and the bucket
- Postgres is not published, and MinIO is bound to the loopback
- the bucket is created by a compose service under a profile, replacing a script
  that guessed its network and asked for an `mc` image tag that does not exist
- the mailer is commented out, having nothing to process while mail is disabled
- upstream's `.github` is removed, those workflows monitor DSW's own images

## License

MIT, from the upstream deployment example. See [LICENSE](LICENSE).
