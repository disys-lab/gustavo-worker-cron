# Changelog

## 0.1.2

- `refresh-identity`'s Nebula credential (used for the reporter POST's
  auth) now falls back to `credential.json` via the same `read_credential`
  gustavo-worker's own worker.py uses, when `NEBULA_USERNAME`/
  `NEBULA_PASSWORD` aren't set. If reporter registration is enabled and
  neither source has a credential, that one step is skipped (logged,
  non-fatal) rather than failing the whole action - `host.json` is still
  written.

## 0.1.1

- `DEVICE_GROUP` now falls back to `host.json`'s own `device_group` field
  (written by the worker itself at boot, or by a prior `refresh-identity`
  run) when the env var isn't set, for `refresh-identity` and
  `update-worker` - only needs to be passed explicitly once per host.

## 0.1.0

- Initial release: `reset-nebula-credentials`, `reset-registry-credentials`,
  `refresh-identity`, `update-worker` actions, selected via the `ACTION`
  env var. Built `FROM ghcr.io/disys-lab/gustavo-worker:2.7.4`.
