# Built on gustavo-worker itself so cron_actions.py can import
# functions/identity/identity.py and functions/docker_engine/docker_engine.py
# directly, rather than reimplementing IP resolution / Docker socket
# handling from scratch. Pinned to a specific tag (not latest) so this
# image's own release cadence doesn't silently pick up a future
# gustavo-worker code change it wasn't built/tested against.
FROM ghcr.io/disys-lab/gustavo-worker:2.7.6

COPY cron_actions.py /worker/cron_actions.py

WORKDIR /worker

ENTRYPOINT ["python", "/worker/cron_actions.py"]
