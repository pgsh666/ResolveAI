from __future__ import annotations

import socket

from redis import Redis
from rq import Queue, Worker

from app.core.config import get_settings
from app.core.observability import configure_tracing, shutdown_tracing


def main() -> None:
    settings = get_settings()
    configure_tracing(settings)
    connection = Redis.from_url(settings.redis_url, health_check_interval=30)
    queue = Queue(settings.task_queue_name, connection=connection)
    worker = Worker(
        [queue],
        connection=connection,
        name=f"resolveai-worker-{socket.gethostname()}",
    )
    try:
        worker.work(with_scheduler=False)
    finally:
        shutdown_tracing()


if __name__ == "__main__":
    main()
