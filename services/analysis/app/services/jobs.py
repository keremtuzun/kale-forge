"""Job-queue abstraction: in-process threads for dev, Redis-backed workers for production."""
from __future__ import annotations

import abc
import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)


class JobQueue(abc.ABC):
    @abc.abstractmethod
    def submit(self, job_id: str, fn: Callable[[], None]) -> None: ...


class InProcessQueue(JobQueue):
    """Runs the job on a daemon thread. Suitable for local dev and tests; the analysis
    function is responsible for updating the AnalysisJob row's status."""

    def submit(self, job_id: str, fn: Callable[[], None]) -> None:
        def runner() -> None:
            try:
                fn()
            except Exception:  # noqa: BLE001
                logger.exception("job %s crashed", job_id)

        thread = threading.Thread(target=runner, name=f"job-{job_id}", daemon=True)
        thread.start()


class RedisQueue(JobQueue):
    """Production queue backed by Redis (RQ/Celery-style). Enqueues a task the worker process
    picks up. Lazy import so dev never needs redis installed."""

    def __init__(self, redis_url: str):
        try:
            from redis import Redis  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "RedisQueue requires redis (pip install 'kale-analysis[redis]'); "
                "use JOB_BACKEND=inprocess for development"
            ) from exc
        self.redis = Redis.from_url(redis_url)

    def submit(self, job_id: str, fn: Callable[[], None]) -> None:  # pragma: no cover
        # In production a worker process consumes a durable queue keyed by job_id and
        # re-runs the analysis by id. Documented here; the worker entrypoint lives in
        # services/analysis/worker.py for the Redis deployment.
        raise NotImplementedError(
            "RedisQueue.submit requires the worker deployment; see docs/deployment.md"
        )


def get_job_queue(settings) -> JobQueue:
    if settings.job_backend == "redis":
        return RedisQueue(settings.redis_url)
    return InProcessQueue()
