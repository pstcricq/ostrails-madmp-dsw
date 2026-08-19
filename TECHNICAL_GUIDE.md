# Technical guide

Why this repository is built the way it is. [README.md](README.md) says how to
run it, this file says what each decision protects.

Everything stated as a measurement was observed on this deployment. Where
something is believed rather than verified, it says so.

## 1. Scope

**A deployment, and nothing else.** No maDMP rules, no quality checks, no
application code, and since 19/08/2026 not even a Dockerfile: the webhook that
receives a rendered document, checks it and commits it lives in `madmp-core`,
which also builds and publishes its image, and this repository names a tag of
it. It held that code from 10/08/2026 until 19/08/2026, and giving it back,
image included, is what makes this boundary true rather than nearly true.

The test of the boundary is simple: nothing here has to change when the webhook
does. A newer engine or newer rules is one value in `.env`.

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

**Its code is not here, and neither is its image.** Both are in madmp-core, and
what this deployment holds is the tag it pulls. The check the webhook runs on a
submitted document needs that repository's engine and its rules files, and
pinning one version pins both: a document is checked by the release the image
names, and the verdict committed beside it in the registry says which.

What stays here is the deployment: the compose service, the four variables it
passes, and the healthcheck. That is the same line as for every other service
in this file.

**Configuration is read once, at startup.** A misconfigured webhook refuses to
start. Reading the variables per request lets it answer `/health` and fail on
the first real submission, which is the moment nobody is watching. Missing and
empty are treated alike because compose always defines what its `environment:`
block lists: a value absent from `.env` arrives as an empty string, not as a
missing variable.

**`REGISTRY_` rather than `GITHUB_`.** Compose lets the shell override `.env`,
and `GITHUB_TOKEN` is a name a developer's shell very often already holds. The
collision would have the webhook commit with someone else's credentials, in
silence.

**Outbound HTTPS to `api.github.com`** is the one thing this deployment does at
runtime that reaches outside the host. Everything else talks on the compose
network, `ghcr.io` being reached only when an image is pulled.

## 7. The image

**It is not built here.** `docker-compose.yml` names
`ghcr.io/pstcricq/ostrails-madmp-core/submission:${MADMP_CORE_VERSION}` and
pulls it, exactly as it pulls DSW's three images, Postgres and MinIO. The
submission service was the only `build:` in this file, and it no longer is.

The Dockerfile went to madmp-core on 19/08/2026, the same day the code did and
for the same reason. Everything the image packages is there: the webhook, the
rules a document is checked against, and the engine that runs them. Its
entrypoint and the extra it installs are declared in that repository's
`pyproject.toml`, so a Dockerfile sitting here was a file in one repository
that broke when another changed, with nothing to say so until the next build.

**What this deployment gained.** No build, so no `git`, no BuildKit secret, and
no token that reads a private *source* repository, which is strictly more than
a deployment ever needed to hold. `MADMP_CORE_VERSION` moves from an `ARG`
inside a Dockerfile to `.env`, beside the five other image versions, which is
where a version pin belongs and where `scripts/setup.sh` already reports it.

**What it cost.** Publishing a fix now takes a tag in madmp-core and a CI run,
where `docker compose build submission` used to be enough. That is the same
trade already accepted on 19/08: rules move rarely, and a deployment that
cannot build is a deployment that cannot drift.

**The package is private**, because the repository is, so a host has to
`docker login ghcr.io` once with a token carrying `read:packages`. A host that
has not fails the pull with a 401 that reads like the image does not exist. A
package's visibility is a setting of its own, separate from the repository's,
so making it public would remove the last credential a deployment needs to
stand up. Not done, and not decided.

**The image runs as an unprivileged user**, built in two stages so pip and its
caches stay out of what runs. Both are properties of the Dockerfile and are
documented where it now lives.

## 8. CI

Three checks, all on the deployment files: `docker compose config` against
`.env.example`, shellcheck on `scripts/setup.sh`, and actionlint on the
workflows.

The distinction the header makes is deliberate. CI can tell whether the
deployment files **parse and resolve**. It cannot tell whether a deployment is
**correct**, which only shows on a real host. Claiming otherwise would be worse
than checking nothing.

There is no Python left to lint or test here. The webhook is checked where it
lives, in madmp-core, and what remains is a compose file, a script and the
workflows, each with the tool that reads it. The token is reduced to
`contents: read`, and the job has a timeout, since a stuck job holds a runner
for six hours by default.

## 9. Known limits

**Idempotence stops above 1 MB.** GitHub's Contents API inlines a file's content
up to 1 MB and answers with an empty `content` above that, and that read is how
a submission is compared with what the registry holds. A DMP that large never
compares equal, so every submission commits again instead of reporting
`unchanged`. Nothing breaks, the guarantee quietly stops holding. Only the read
is concerned, a commit carries its files as tree entries and has no such limit.

**The image's dependencies are resolved at build time, not locked.** The
webhook used to install from a hash-pinned `requirements.txt` generated from a
lockfile. Installing madmp-core from its own source pins that repository
exactly and leaves its transitive dependencies to pip, so two builds of the
same tag can differ. This deployment no longer builds, so it is spared the
consequence, a pulled image being one set of bytes whoever pulls it. What would
close it is madmp-core installing from its committed lockfile in the image, and
nothing needs it yet.

**No retry and no rate-limit handling.** GitHub answering 403 or 429 surfaces as
a 502 to DSW. Acceptable for one instance submitting occasionally.

**`api_url` on the GitHub client is parameterisable and unused.** It would allow
GitHub Enterprise. Nothing passes it, including the tests.

**The demo accounts are a manual step.** DSW seeds three whose credentials are
published. The setup script warns for as long as they answer, and that is all it
does.

## 10. Open decisions

- **`S3_URL` behind a proxy.** The browser fetches documents straight from
  MinIO through a presigned URL, so MinIO needs a public address. A subdomain
  avoids rewriting paths, since MinIO puts the bucket name in the path. This is
  the one value of the three that does not follow from the others.
- **Managed Postgres or S3.** If the infrastructure provides either, the change
  is not a value in `.env` but the removal of services from the compose file.
- **A reverse proxy on another machine.** Everything is bound to `127.0.0.1`,
  which suits a proxy on the same host. Anything else means widening the
  binding and letting a firewall take over.
