"""Container healthcheck for Redis connectivity and a live RQ worker."""

from redis import Redis
from rq import Queue, Worker

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=3)
    connection.ping()
    queue = Queue(settings.task_queue_name, connection=connection)
    if Worker.count(queue=queue) < 1:
        raise SystemExit("No active RQ worker")
    print("RQ worker healthy")


if __name__ == "__main__":
    main()
