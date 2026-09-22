"""
One-shot maintenance actions for a gustavo-worker deployment, selected by
the ACTION env var. Meant to be invoked by the host's own cron/systemd-timer
(not baked into this image) - each action is independent and can be
scheduled on its own, in any combination with the others:

  ACTION=reset-nebula-credentials    NEBULA_USERNAME/NEBULA_PASSWORD
  ACTION=reset-registry-credentials  REGISTRY_USERNAME/REGISTRY_PASSWORD/REGISTRY_HOST
  ACTION=refresh-identity             DEVICE_GROUP (only required before host.json
                                      exists - see _resolve_device_group),
                                      NEBULA_USERNAME/PASSWORD (only required for the
                                      reporter POST's auth if credential.json doesn't
                                      already have one - see read_credential),
                                      REPORTER_HOST/REPORTER_PORT/REPORTER_PROTOCOL (optional)
  ACTION=update-worker                DEVICE_GROUP (same host.json fallback) or
                                      WORKER_CONTAINER_NAME,
                                      WORKER_IMAGE/WORKER_VERSION_TAG (optional),
                                      REGISTRY_USERNAME/PASSWORD/HOST (optional)
  ACTION=change-device-group          NEW_DEVICE_GROUP, DEVICE_GROUP (same host.json
                                      fallback) or WORKER_CONTAINER_NAME,
                                      NEBULA_USERNAME/PASSWORD and
                                      REPORTER_HOST/PORT/PROTOCOL (optional, for
                                      reporter registration/cleanup - same fallback
                                      as refresh-identity)

The two credential actions read-modify-write the same credential.json -
each only ever touches its own half of the file (Nebula vs. registry
fields), so running one never wipes out what the other last wrote.

gustavo-worker's own identity.py/docker_engine.py are reused as-is
(unmodified) for path constants and IP/Docker helpers - see this image's
Dockerfile, which is built FROM gustavo-worker itself.
"""
import json
import os
import sys
import uuid

import requests

from functions.identity.identity import (
    CREDENTIAL_JSON_PATH, HOST_JSON_PATH, _get_host_ip, _get_remote_ip, read_credential,
)
from functions.docker_engine.docker_engine import DockerFunctions


def _load_credential_file():
    """
    Read the existing credential.json, or {} if missing/unreadable.

    Shared by both credential actions below - each only overwrites its
    own half of this dict, never the whole file, so the two actions can
    run on independent schedules without one wiping the other's fields
    out.
    """
    try:
        with open(CREDENTIAL_JSON_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_credential_file(data):
    """Write `data` to credential.json, creating its directory if needed, 0600 permissions."""
    os.makedirs(os.path.dirname(CREDENTIAL_JSON_PATH), exist_ok=True)
    try:
        with open(CREDENTIAL_JSON_PATH, "w") as f:
            json.dump(data, f)
        os.chmod(CREDENTIAL_JSON_PATH, 0o600)
    except Exception as e:
        print(e, file=sys.stderr)
        print(f"failed writing {CREDENTIAL_JSON_PATH}", file=sys.stderr)
        sys.exit(2)


def _resolve_device_group():
    """
    DEVICE_GROUP env var if set, else read back from host.json (written
    there by the worker itself at boot, or by a prior refresh-identity
    run) - so a crontab entry only needs to pass DEVICE_GROUP explicitly
    if host.json doesn't already exist yet. Returns None if neither
    source has it.
    """
    env_value = os.environ.get("DEVICE_GROUP")
    if env_value:
        return env_value
    if os.path.exists(HOST_JSON_PATH):
        try:
            with open(HOST_JSON_PATH) as f:
                return json.load(f).get("device_group")
        except Exception as e:
            print(e, file=sys.stderr)
            print(f"failed reading device_group back from {HOST_JSON_PATH}", file=sys.stderr)
    return None


def reset_nebula_credentials():
    """Rewrite only the username/password fields of credential.json, preserving any registry_* fields already there."""
    data = _load_credential_file()
    data["username"] = os.environ["NEBULA_USERNAME"]
    data["password"] = os.environ["NEBULA_PASSWORD"]
    _write_credential_file(data)
    print(f"reset-nebula-credentials: wrote {CREDENTIAL_JSON_PATH}")


def reset_registry_credentials():
    """Rewrite only the registry_username/registry_password/registry_host fields of credential.json, preserving username/password already there."""
    data = _load_credential_file()
    data["registry_username"] = os.environ.get("REGISTRY_USERNAME")
    data["registry_password"] = os.environ.get("REGISTRY_PASSWORD")
    data["registry_host"] = os.environ.get("REGISTRY_HOST")
    _write_credential_file(data)
    print(f"reset-registry-credentials: wrote {CREDENTIAL_JSON_PATH}")


def refresh_identity(device_group=None):
    """
    Rewrite host.json (host/remote IP) and re-register with reporter if configured.

    Does not touch credential.json. Reuses the existing node_id from
    host.json if present - never regenerates it - same as
    gustavo-worker's own bootstrap_identity. Reporter registration is
    best-effort: a failure there is logged but does not fail this
    action, since host.json itself was already written successfully.

    Parameters
    ----------
    device_group : str, optional
        Overrides `_resolve_device_group()` - used by `change_device_group`
        to write the *new* device group before host.json's own stale
        value would otherwise be read back. When not given (the ACTIONS
        dispatch case), DEVICE_GROUP is only required the first time
        (before host.json exists) - see `_resolve_device_group`.
        NEBULA_USERNAME/NEBULA_PASSWORD are likewise only needed as a
        fallback for the reporter POST's auth if credential.json doesn't
        already have a Nebula credential in it - see `read_credential`.
    """
    if device_group is None:
        device_group = _resolve_device_group()
    if device_group is None:
        print("refresh-identity: DEVICE_GROUP must be set (no existing host.json to read it back from)", file=sys.stderr)
        sys.exit(2)

    if os.path.exists(HOST_JSON_PATH):
        try:
            with open(HOST_JSON_PATH) as f:
                node_id = json.load(f)["node_id"]
        except Exception as e:
            print(e, file=sys.stderr)
            print("failed reading existing host.json node_id - generating a new one", file=sys.stderr)
            node_id = str(uuid.uuid4())
    else:
        node_id = str(uuid.uuid4())

    host_ip = _get_host_ip()
    remote_ip = _get_remote_ip()

    os.makedirs(os.path.dirname(HOST_JSON_PATH), exist_ok=True)
    try:
        with open(HOST_JSON_PATH, "w") as f:
            json.dump({"node_id": node_id, "host_ip": host_ip, "remote_ip": remote_ip,
                      "device_group": device_group}, f)
    except Exception as e:
        print(e, file=sys.stderr)
        print(f"failed writing {HOST_JSON_PATH}", file=sys.stderr)
        sys.exit(2)
    print(f"refresh-identity: wrote {HOST_JSON_PATH} (node_id={node_id})")

    reporter_host = os.environ.get("REPORTER_HOST")
    if reporter_host is None:
        return
    reporter_port = os.environ.get("REPORTER_PORT")
    reporter_protocol = os.environ.get("REPORTER_PROTOCOL", "http")
    # Same fallback gustavo-worker's own read_credential already gives
    # worker.py: prefers whatever's currently in credential.json (the
    # source of truth reset-nebula-credentials and the worker itself
    # maintain), falling back to these env vars only if the file doesn't
    # have it yet.
    username, password = read_credential(os.environ.get("NEBULA_USERNAME"), os.environ.get("NEBULA_PASSWORD"))
    if not username or not password:
        print("refresh-identity: no Nebula credential available (neither credential.json nor "
              "NEBULA_USERNAME/NEBULA_PASSWORD) - skipping reporter registration", file=sys.stderr)
        return
    auth = (username, password)
    try:
        resp = requests.post(
            f"{reporter_protocol}://{reporter_host}:{reporter_port}/api/directory/{device_group}",
            json={"node_id": node_id, "host_ip": host_ip, "remote_ip": remote_ip},
            auth=auth, timeout=10,
        )
        if resp.status_code != 200:
            print(f"refresh-identity: reporter registration failed: HTTP {resp.status_code} {resp.text}", file=sys.stderr)
        else:
            print("refresh-identity: reporter registration updated")
    except Exception as e:
        print(e, file=sys.stderr)
        print("refresh-identity: reporter registration failed", file=sys.stderr)


def update_worker():
    """
    Pull a new gustavo-worker image tag and recreate the running worker container on it.

    Clones the running container's own Config/HostConfig (env vars,
    volume binds, network mode, restart policy) rather than re-deriving
    them from scratch, so it's robust to anything an admin hand-edited
    on top of what gustavo's own worker-compose/worker-script generators
    produced. A no-op if the container is already running the resolved
    image digest for the requested tag.

    DEVICE_GROUP falls back to host.json if not set explicitly - see
    `_resolve_device_group`. Only needed at all when WORKER_CONTAINER_NAME
    isn't given directly.
    """
    device_group = _resolve_device_group()
    container_name = os.environ.get("WORKER_CONTAINER_NAME") or (
        f"worker_{device_group}" if device_group else None
    )
    if container_name is None:
        print("update-worker: WORKER_CONTAINER_NAME or DEVICE_GROUP must be set", file=sys.stderr)
        sys.exit(2)

    image = os.environ.get("WORKER_IMAGE", "ghcr.io/disys-lab/gustavo-worker")
    tag = os.environ.get("WORKER_VERSION_TAG", "latest")
    new_image_ref = f"{image}:{tag}"

    docker_socket = DockerFunctions()
    docker_socket.registry_login(
        registry_host=os.environ.get("REGISTRY_HOST", ""),
        registry_user=os.environ.get("REGISTRY_USERNAME"),
        registry_pass=os.environ.get("REGISTRY_PASSWORD"),
    )

    if not docker_socket.pull_image(image, version_tag=tag):
        print(f"update-worker: pull of {new_image_ref} failed, leaving {container_name} as-is", file=sys.stderr)
        sys.exit(2)

    try:
        inspection = docker_socket.cli.inspect_container(container_name)
    except Exception as e:
        print(e, file=sys.stderr)
        print(f"update-worker: no running container named {container_name!r} to update", file=sys.stderr)
        sys.exit(2)

    if inspection["Config"]["Image"] == new_image_ref and \
       docker_socket.cli.inspect_image(new_image_ref)["Id"] == inspection["Image"]:
        print(f"update-worker: {container_name} is already on {new_image_ref}, nothing to do")
        return

    host_config = inspection["HostConfig"]
    docker_socket.stop_and_remove_container(container_name)
    docker_socket.cli.create_container(
        image=new_image_ref, name=container_name,
        environment=inspection["Config"]["Env"],
        host_config=host_config,
        labels=inspection["Config"].get("Labels", {}),
    )
    docker_socket.start_container(container_name)
    print(f"update-worker: {container_name} recreated on {new_image_ref}")


def change_device_group():
    """
    Move this worker to a different device group: recreate its container
    under the new worker_<device_group> name, refresh its identity to
    match, and clean up its stale reporter directory entry under the old
    device group.

    A no-op if NEW_DEVICE_GROUP already matches the worker's current
    device group - same idempotency shape as `update_worker`'s
    image-digest check.

    Three steps, one call, since leaving any of them undone would leave
    the worker in an inconsistent state: the container swap alone would
    leave host.json/the reporter directory pointing at the old device
    group; skipping the reporter cleanup would leave a stale ghost entry
    under the old device group forever.
    """
    new_device_group = os.environ["NEW_DEVICE_GROUP"]
    device_group = _resolve_device_group()
    if device_group is None:
        print("change-device-group: DEVICE_GROUP must be set (no existing host.json to read it back from)", file=sys.stderr)
        sys.exit(2)
    if new_device_group == device_group:
        print(f"change-device-group: already on {device_group!r}, nothing to do")
        return

    old_container_name = os.environ.get("WORKER_CONTAINER_NAME") or f"worker_{device_group}"
    new_container_name = f"worker_{new_device_group}"

    docker_socket = DockerFunctions()
    try:
        inspection = docker_socket.cli.inspect_container(old_container_name)
    except Exception as e:
        print(e, file=sys.stderr)
        print(f"change-device-group: no running container named {old_container_name!r} to move", file=sys.stderr)
        sys.exit(2)

    # Clone the running container's own env vars, swapping only DEVICE_GROUP's
    # value - same clone-then-recreate approach as update_worker, robust to
    # anything hand-edited on top of gustavo's own generators.
    env_list = [e for e in inspection["Config"]["Env"] if not e.startswith("DEVICE_GROUP=")]
    env_list.append(f"DEVICE_GROUP={new_device_group}")
    image_ref = inspection["Config"]["Image"]
    host_config = inspection["HostConfig"]

    docker_socket.stop_and_remove_container(old_container_name)
    docker_socket.cli.create_container(
        image=image_ref, name=new_container_name,
        environment=env_list,
        host_config=host_config,
        labels=inspection["Config"].get("Labels", {}),
        command=inspection["Config"]["Cmd"],
    )
    docker_socket.start_container(new_container_name)
    print(f"change-device-group: recreated as {new_container_name} (DEVICE_GROUP={new_device_group})")

    # host.json's node_id is read fresh inside refresh_identity - reused as-is,
    # not regenerated, so this is a device group change, not a new identity.
    with open(HOST_JSON_PATH) as f:
        node_id = json.load(f)["node_id"]
    refresh_identity(device_group=new_device_group)

    # Best-effort cleanup of the stale entry under the old device group -
    # logged, non-fatal, since the container move and identity refresh
    # above (the parts that matter for the worker to actually function)
    # already succeeded by this point.
    reporter_host = os.environ.get("REPORTER_HOST")
    if reporter_host is None:
        return
    reporter_port = os.environ.get("REPORTER_PORT")
    reporter_protocol = os.environ.get("REPORTER_PROTOCOL", "http")
    username, password = read_credential(os.environ.get("NEBULA_USERNAME"), os.environ.get("NEBULA_PASSWORD"))
    if not username or not password:
        print("change-device-group: no Nebula credential available - skipping stale reporter entry cleanup", file=sys.stderr)
        return
    try:
        resp = requests.delete(
            f"{reporter_protocol}://{reporter_host}:{reporter_port}/api/directory/{device_group}/{node_id}",
            auth=(username, password), timeout=10,
        )
        if resp.status_code != 200:
            print(f"change-device-group: cleanup of stale entry under {device_group!r} failed: "
                  f"HTTP {resp.status_code} {resp.text}", file=sys.stderr)
        else:
            print(f"change-device-group: removed stale reporter entry under {device_group!r}")
    except Exception as e:
        print(e, file=sys.stderr)
        print(f"change-device-group: cleanup of stale entry under {device_group!r} failed", file=sys.stderr)


ACTIONS = {
    "reset-nebula-credentials": reset_nebula_credentials,
    "reset-registry-credentials": reset_registry_credentials,
    "refresh-identity": refresh_identity,
    "update-worker": update_worker,
    "change-device-group": change_device_group,
}


if __name__ == "__main__":
    action = os.environ.get("ACTION")
    if action not in ACTIONS:
        print(f"unknown or missing ACTION {action!r} - expected one of {list(ACTIONS)}", file=sys.stderr)
        sys.exit(2)
    ACTIONS[action]()
