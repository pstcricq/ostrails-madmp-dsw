# Technical guide

Why this repository is built the way it is. [README.md](README.md) says how to
run it, this file says what each decision protects.

Everything stated as a measurement was observed on this deployment. Where
something is believed rather than verified, it says so.

## 1. Scope

A deployment plus one small application. No maDMP rules, no quality checks,
nothing from `madmp-core`: the webhook receives a document that is already
rendered and commits it. That boundary is what keeps the whole thing under a
thousand lines.

The upstream deployment example is kept as the `upstream` remote so later DSW
releases can be compared, not merged.

## 2. Configuration comes from the environment

DSW resolves a setting from the environment first, then its config file, then a
default compiled into the image. That order is what makes an `application.yml`
unnecessary, and doing without one is the most consequential decision here.

The example that ships with DSW puts **working signing secrets in a public
repository**. An instance that keeps them can have a valid token forged against
it by anyone who read that example. Going through the environment gives the
secrets exactly one home, `.env`, which is gitignored.

The image still carries its own `application.yml` underneath, and its defaults
are not neutral: it names the database `wizard` where this deployment uses
`engine-wizard`. Every value that matters is therefore set explicitly rather
than left to a fallback.

There is no `:-default` anywhere in `docker-compose.yml`. A default written
there would be a second home for a value whose home is `.env.example`, free to
drift from it. A variable missing from `.env` resolves to an empty string and
the stack fails saying so, which is the failure we want.

## 3. `.env` is filled by hand

No script writes a secret. `.env.example` carries, next to each empty value,
what it is and the command that produces it.

The example never ships a working secret, and never a placeholder password
either. "Empty" is what forces a choice: a value in the example is a value
every deployment inherits in silence, and one of them would be running the
object store on a password published in a git repository.

Three constraints in that file are measured rather than assumed:

- MinIO refuses to start on a password shorter than 8 characters, and names the
  variable when one is missing.
- `GENERAL_SECRET` must be exactly 32 ASCII characters, which is what
  `openssl rand -hex 16` produces.
- The RSA key is the one multi-line value, and it has to be pasted between
  double quotes. Without them, compose reads every following line as a new
  variable and refuses the whole file, so **none** of the nineteen values is
  read. `scripts/setup.sh` runs `docker compose config --quiet` before anything
  else because that is the only check which catches this: no individual key
  looks wrong.

## 4. The compose file

**Project name pinned.** Container, volume and network names survive a folder
rename.

**Named volumes.** Without them, `docker compose down` destroys the database
and the bucket.

**The server's healthcheck is redefined.** The DSW server image ships one with a
300 second interval and no start period. Docker runs the first check only after
one full interval, so the container cannot report healthy in under five minutes
however fast it answers, and a failure takes up to five minutes to surface.
Measured, deploying from an empty database:

| | |
|---|---|
| with the image's healthcheck | 307 s |
| with ours, 10 s interval and a 300 s start period | **12 s** |
| the 67 migrations themselves | 7 s |

The migrations are not the cost.

**Postgres has a healthcheck too**, so `server` and `docworker` wait on a
database that answers rather than on a container that exists. It uses
`pg_isready -h 127.0.0.1`: over the unix socket, the temporary server that
`initdb` runs already answers, and the check would go green before the database
accepts a single TCP connection.

**`platform: linux/amd64` on the server only.** Verified against the published
manifests: `wizard-server:4.31` is amd64-only, while `wizard-client`,
`document-worker` and `mailer` are multi-arch with an arm64 variant. Adding the
line to the other three puts them under emulation for nothing, removing it from
the server breaks it on an ARM machine.

**`createbucket` is a compose service under a profile.** DSW does not create its
bucket: in the S3 API, CreateBucket and PutObject are separate operations, and
writing to a missing bucket returns NoSuchBucket. A compose service knows its
own network, reads `.env` and resolves `${MC_VERSION}`, where a shell script
would have to guess all three. The profile keeps it out of `up`, which would
otherwise treat a one-shot task as a service that keeps stopping.

**The mailer is present but commented out.** With mail disabled it has nothing
to process. What that costs: no password reset and no e-mail invitation, both
already impossible without mail. Uncomment it together with enabling mail, or
the server queues mailer commands nobody processes while the interface reports
messages as sent.

**`engine-wizard` is both the database name and the bucket name, and that is a
coincidence.** They are unrelated namespaces, and the image proves it: its own
default for the database is `wizard`. Either can be renamed without the other.

**Everything is bound to `127.0.0.1`.** Publishing on `0.0.0.0` exposes the
service to the whole network the machine sits on, and on Linux it bypasses
`ufw` entirely.

**There is no nginx override for the client.** The client image redirects `/` to
an absolute `http://` address built from the protocol nginx itself listens on,
which breaks behind a TLS terminator. Shadowing the image's own config file to
repair one address costs more than it fixes: behind a proxy, the redirect
belongs to the proxy, with `proxy_redirect http:// https://`.

## 5. The setup script

It reports the configuration, then runs two commands. It writes nothing, which
is what makes it re-runnable without a single idempotence check.

It reports the value **compose will use**, not the one in `.env`: the
environment wins over that file, so reading the file alone would announce a
value the containers never see. Secrets are reported as `set`, never printed,
and the eleven values that are not secrets are printed in full, because that is
where a laptop URL left in a server's `.env` becomes visible.

It stops before starting anything when a value is missing, and names every one
of them at once.

Nothing here waits for the API: `up --wait` returns when the healthchecks pass,
which is the same information obtained from the thing that already knows it.

`.gitignore` covers `.env.*` and not just `.env`, because a second environment
file is the natural thing to create when deploying to two targets, and it holds
the same secrets.

## 6. The webhook

**Flat modules.** `app.py`, `service.py`, `github_client.py`, no package. That
is how they are laid out inside the image, and the tests import them the same
way, so nothing depends on a layout the container does not have.

**Configuration read once, at startup.** A misconfigured webhook refuses to
start. Reading the variables per request lets it answer `/health` and fail on
the first real submission, which is the moment nobody is watching. Missing and
empty are treated alike because compose always defines what its `environment:`
block lists: a value absent from `.env` arrives as an empty string, not as a
missing variable. All four are reported together, so a fresh deployment is fixed
in one pass rather than one restart per variable.

**`REGISTRY_` rather than `GITHUB_`.** Compose lets the shell override `.env`,
and `GITHUB_TOKEN` is a name a developer's shell very often already holds. The
collision would have the webhook commit with someone else's credentials, in
silence.

**The DMP and its provenance are one commit.** The rendered document carries a
`metadata` object beside `dmp`. The webhook takes it out, which purifies the
DMP and yields the block in the same gesture, and writes both files through
the Git Data API: the parent commit is read for its tree, a tree is built over
it, a commit over that tree, and the branch reference is moved once. Nothing
points at the tree or the commit until that move, so the two files land
together or not at all. Two calls of the Contents API would leave a DMP whose
rules versions are missing whenever the second fails, and there is no state
here to repair it with.

**A submission is offered, not merged.** It lands on `submission/<folder>` and
a pull request carries it, so the registry's default branch only ever holds
documents its quality control has passed. What decides where a submission
builds on is whether a pull request is open for that branch: with one, the
branch holds a review in progress and the submission continues it, without
one, the branch is absent or left over from a merged review and the submission
starts again from the default branch. The comparison that makes a resubmission
idempotent reads the same place, which is why `get_file` takes a ref.

The reference is moved **with `force`**. Starting again from the default branch
while the branch still holds a merged review is not a fast-forward, and nothing
is lost that the merge did not already carry. This is the one place the webhook
rewrites history, and it rewrites only its own branches.

**One pull request per project, not per submission.** A researcher who submits
five times has one place to look, and the fifth replaces the fourth. GitHub
answers 422 both for a branch already under review and for a branch with
nothing to offer, so the open list is read after the refusal, which tells the
two apart and costs one call rather than two in the common case.

**A 404 is an absence on a read and a failure on a write.** The transport
raises on every error status, and `get_file` alone catches the 404 and reads it
as an absence. Reading it as an absence in the transport would mean a write
GitHub refused, for a revoked token, a renamed repository or a wrong
`REGISTRY_OWNER`, returns nothing instead of raising, and the webhook answers
200 with `"action": "created"` and a link to a file that was never written. DSW
would display a successful submission and the DMP would be lost silently.

GitHub also answers 404 for a repository the token cannot see, so an absence
means "not there, or not visible with this token", which is why the error about
an uninitialized folder mentions both.

**Blocking I/O goes to a thread pool.** The GitHub client is synchronous and
stdlib-only, and the endpoint has to be `async` to read the request body.
FastAPI only moves `def` endpoints to a thread pool, not `async def` ones, so
awaiting the call directly freezes the event loop, and with it `/health`, for up
to the 30 second timeout.

**The token is compared with `hmac.compare_digest`, on bytes.** Constant time
removes a timing attack that is theoretical here but free to close. Bytes are
required: `compare_digest` refuses non-ASCII strings, and the header is whatever
the caller sent, so comparing strings turns a malformed token into a 500.

**The registry branch is a named constant.** It ends up inside every `dmp_id`,
which is the DMP's stable identifier, so moving the registry to another branch
leaves every identifier ever issued pointing nowhere. It is also what a
submission is offered against, and what it starts again from. A `dmp_id` is
written before the merge that makes it resolve, so it is a promise, and the
pull request is what keeps it.

**A malformed envelope is refused, never defaulted.** The project it names must
be the folder the submission was routed to, which catches one project's
document submitted through another's service, and its pins must be a non-empty
list of one-key mappings of strings, the shape quality control resolves into
file paths. A DMP whose rules versions are unknown cannot be checked against
them.

**No GitHub account is named in the repository.** `REGISTRY_OWNER` and
`REGISTRY_REPO` ship empty. The tests use fixed values because they need
something, and they are the only place a name appears.

## 7. The image

`submission/Dockerfile` installs from `submission/requirements.txt`, generated
from `uv.lock` by `uv export`, with `--require-hashes`. CI regenerates the file
and fails on a diff, so drift is impossible rather than discouraged.

The hashes are what make it worth having: without them the file pins versions,
with them the build refuses anything whose content is not what the lock
resolved.

Pinning versions directly in the Dockerfile looks equivalent and is not.
Pinning `fastapi` leaves `starlette`, `pydantic` and twenty others floating, so
the build stays irreproducible while gaining a third list to keep in step.

The base image tag floats on purpose, where every other image is pinned to a
patch release. `python:3.12-slim` carries Debian's security updates without
intervention, and what determines behaviour is pinned by hash below it.

The container runs as an unprivileged user. It reads a request and writes no
file, so it needs nothing, and root inside a container is root on the host
kernel for anything that escapes it.

## 8. Tests

39 tests, no network, no Docker.

**The GitHub double is deliberately strict.** `FakeGitHub` raises a 409 when
asked to write over an existing file without a `sha`, because that is what
GitHub does. A double that accepts more than the real thing lets code pass the
suite and fail in production: removing `sha=existing["sha"]` from `service.py`
breaks every update, and two tests catch it.

**One section reaches the real client**, with `urlopen` replaced. Everything
else runs against the double, so the transport, the base64 encoding and the
status handling would otherwise be untested, and that is where a wrong status
reading costs a DMP.

`/health` is tested because the compose healthcheck depends on it: `up --wait`
and the container's restart both hang on it answering.

## 9. CI

Six checks. Three on the code, ruff twice and pytest, and three on the
deployment: the requirements against the lockfile, `docker compose config`, and
shellcheck on `scripts/setup.sh`.

The distinction the header makes is deliberate. CI can tell whether the
deployment files **parse and resolve**. It cannot tell whether a deployment is
**correct**, which only shows on a real host. Claiming otherwise would be worse
than checking nothing.

`uv sync --frozen` means the committed lockfile decides the versions, so a
transitive release cannot turn CI red on its own. The token is reduced to
`contents: read`, and the job has a timeout, since a stuck job holds a runner
for six hours by default.

Ruff runs on its default rule set, deliberately. Measured on version 0.16.2:
413 rules are active by default, including import order, the whole `UP` family
and 58 bugbear rules. The `B9xx` rules, which flake8-bugbear itself classes as
opinionated, are not among them. Adding a `select` to reach them would mean
claiming to know better than the tool on a codebase that passes its defaults.

## 10. Known limits

**Idempotence stops above 1 MB.** GitHub's Contents API inlines a file's content
up to 1 MB and answers with an empty `content` above that, and that read is how
a submission is compared with what its review already offers. A DMP that large
never compares equal, so every submission commits again instead of reporting
`unchanged`.
Nothing breaks, the guarantee quietly stops holding. Only the read is
concerned, a commit carries its files as tree entries and has no such limit.

**No retry and no rate-limit handling.** GitHub answering 403 or 429 surfaces as
a 502 to DSW. Acceptable for one instance submitting occasionally.

**`api_url` on the GitHub client is parameterisable and unused.** It would allow
GitHub Enterprise. Nothing passes it, including the tests.

**The demo accounts are a manual step.** DSW seeds three whose credentials are
published. The setup script warns for as long as they answer, and that is all it
does.

## 11. Open decisions

- **`S3_URL` behind a proxy.** The browser fetches documents straight from
  MinIO through a presigned URL, so MinIO needs a public address. A subdomain
  avoids rewriting paths, since MinIO puts the bucket name in the path. This is
  the one value of the three that does not follow from the others.
- **Managed Postgres or S3.** If the infrastructure provides either, the change
  is not a value in `.env` but the removal of services from the compose file.
- **A reverse proxy on another machine.** Everything is bound to `127.0.0.1`,
  which suits a proxy on the same host. Anything else means widening the
  binding and letting a firewall take over.
