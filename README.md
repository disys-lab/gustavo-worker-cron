# gustavo-worker-cron

Maintenance companion image for [gustavo-worker](https://github.com/disys-lab/gustavo_worker).
Built `FROM` gustavo-worker itself, so it can reuse its
`functions/identity/identity.py` and `functions/docker_engine/docker_engine.py`
directly rather than reimplementing IP resolution or Docker socket
handling from scratch.

## What this is

A one-shot dispatcher, not a persistent daemon - meant to be invoked by
the host's own cron/systemd-timer, not scheduled from inside the image.
Four independent actions, selected by the `ACTION` env var:

| `ACTION` | Does | Needs |
|---|---|---|
| `reset-nebula-credentials` | Rewrites only the `username`/`password` fields of `credential.json` | `NEBULA_USERNAME`, `NEBULA_PASSWORD` |
| `reset-registry-credentials` | Rewrites only the `registry_username`/`registry_password`/`registry_host` fields of `credential.json` | `REGISTRY_USERNAME`, `REGISTRY_PASSWORD`, `REGISTRY_HOST` |
| `refresh-identity` | Rewrites `host.json` (host/remote IP), re-registers with reporter if configured | `NEBULA_USERNAME`, `NEBULA_PASSWORD`, `DEVICE_GROUP`\*; optionally `REPORTER_HOST`/`REPORTER_PORT`/`REPORTER_PROTOCOL` |
| `update-worker` | Pulls a new gustavo-worker image tag and recreates the running worker container on it | `DEVICE_GROUP`\* (or `WORKER_CONTAINER_NAME`); optionally `WORKER_IMAGE`/`WORKER_VERSION_TAG`, `REGISTRY_USERNAME`/`REGISTRY_PASSWORD`/`REGISTRY_HOST` |

\* `DEVICE_GROUP` only needs to be passed explicitly the first time - once
`host.json` exists (written by the worker itself at boot, or by a prior
`refresh-identity` run), both actions read it back from there if the env
var isn't set.

Each action is fully independent and can be scheduled on its own, in any
combination with the others. The two credential actions read-modify-write
the shared `credential.json` - each only ever touches its own half of the
file, so running one never wipes out what the other last wrote.

## Usage

Every invocation is the same image; `ACTION` plus whatever other `-e`
flags that action needs is the entire interface - no CLI subcommand.

```bash
# crontab -e, on the same host the worker itself runs on:

# hourly: keep remote_ip/reporter registration current
0 * * * * docker run --rm \
  -v ~/.gustavo-worker:/etc/gustavo-worker \
  -e ACTION=refresh-identity \
  -e DEVICE_GROUP=mydevicegroup -e NEBULA_USERNAME=... -e NEBULA_PASSWORD=... \
  -e REPORTER_HOST=... -e REPORTER_PORT=8090 \
  ghcr.io/disys-lab/gustavo-worker-cron:latest

# weekly: restore just the Nebula half of credential.json
0 4 * * 0 docker run --rm \
  -v ~/.gustavo-worker:/etc/gustavo-worker \
  -e ACTION=reset-nebula-credentials \
  -e NEBULA_USERNAME=... -e NEBULA_PASSWORD=... \
  ghcr.io/disys-lab/gustavo-worker-cron:latest

# weekly (independent trigger/schedule): restore just the registry half
0 5 * * 0 docker run --rm \
  -v ~/.gustavo-worker:/etc/gustavo-worker \
  -e ACTION=reset-registry-credentials \
  -e REGISTRY_USERNAME=... -e REGISTRY_PASSWORD=... -e REGISTRY_HOST=... \
  ghcr.io/disys-lab/gustavo-worker-cron:latest

# monthly: pull/recreate the worker container on a new image tag
0 3 1 * * docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e ACTION=update-worker \
  -e DEVICE_GROUP=mydevicegroup \
  -e WORKER_VERSION_TAG=2.7.4 \
  ghcr.io/disys-lab/gustavo-worker-cron:latest
```

Only `update-worker` needs the Docker socket mount; the two credential
actions need only the shared `~/.gustavo-worker` mount (the same host
path gustavo's own worker-compose/worker-script downloads use); `refresh-identity`
needs that mount plus reporter reachability.

## `update-worker` mechanics

Clones the running `worker_<device_group>` container's own env vars,
volume binds, network mode, and restart policy from `docker inspect`
rather than re-deriving them from scratch - robust to anything hand-edited
on top of what gustavo's own generators produced. A no-op if the
container is already running the resolved image digest for the
requested tag.

## Not in scope

No credential *rotation against Nebula Manager* (fetching a genuinely new
password/token) - "reset" here means restoring `credential.json` to
whatever values this container itself is invoked with. Real upstream
rotation would need Nebula Manager-side rotation support to begin with.
