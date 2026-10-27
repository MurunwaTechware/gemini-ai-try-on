"""Job queue: how measurement jobs reach workers. Each job is handled by exactly one worker.

How the waiting page hears back isn't the queue's job: workers update the job's status record in
Firestore (job_status.py), and the web server holding the page's stream listens to that record.

JobQueue is the interface main.py and worker.py use. RabbitMQQueue is the only implementation
for now (local Docker); a cloud provider means one more class with the same methods:
  Google Pub/Sub  a topic with one shared worker subscription (infra/terraform/pubsub.tf)
  AWS             an SQS queue
"""
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustConnection

import config

log = logging.getLogger(__name__)

# Messages carry {"user_id", "job_id"}: the job's folder is RUNS_DIR/<user ID>/measurement_profiles/<job ID>
JobHandler = Callable[[str, str], Awaitable[None]]   # called with (user ID, job ID); returns once the job is done


class JobQueue(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def publish_job(self, user_id: str, job_id: str) -> None: ...
    async def consume_jobs(self, handler: JobHandler, concurrency: int) -> None: ...


class RabbitMQQueue:
    JOBS_QUEUE = "measure_jobs"      # durable: queued jobs survive a RabbitMQ restart
    # Startup waits roughly ATTEMPTS x (RETRY_S + time per failed attempt) for the broker. A refused
    # connection itself takes ~4 s on Windows (it tries ::1, then 127.0.0.1), so ~70 s in total there.
    CONNECT_ATTEMPTS = 10
    CONNECT_RETRY_S = 3

    def __init__(self, url: str = config.RABBITMQ_URL):
        self._url = url
        self._connection: AbstractRobustConnection | None = None

    async def connect(self) -> None:
        # "Robust" reconnects on its own and restores queues and consumers after a broker restart,
        # but only once a first connection has succeeded. The first attempt fails straight away,
        # so retry here for a broker that is stopped or still booting (e.g. just started in Docker).
        for attempt in range(1, self.CONNECT_ATTEMPTS + 1):
            try:
                self._connection = await aio_pika.connect_robust(self._url)
                break
            except ConnectionError as e:   # refused, or accepted then closed while RabbitMQ boots
                if attempt == self.CONNECT_ATTEMPTS:
                    raise ConnectionError(f"RabbitMQ unreachable at {self._url.split('@')[-1]} after "
                                          f"{attempt} attempts. Is the broker running?") from e
                log.warning("RabbitMQ not ready (%s); retrying in %d s (%d/%d)",
                            e, self.CONNECT_RETRY_S, attempt, self.CONNECT_ATTEMPTS)
                await asyncio.sleep(self.CONNECT_RETRY_S)
        self._channel = await self._connection.channel()
        self._jobs = await self._channel.declare_queue(self.JOBS_QUEUE, durable=True)

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()

    async def publish_job(self, user_id: str, job_id: str) -> None:
        # Persistent, so a job that is queued but not yet picked up survives a broker restart
        message = aio_pika.Message(json.dumps({"user_id": user_id, "job_id": job_id}).encode(),
                                   delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
        await self._channel.default_exchange.publish(message, routing_key=self.JOBS_QUEUE)

    async def consume_jobs(self, handler: JobHandler, concurrency: int) -> None:
        # Prefetch caps how many unacknowledged jobs RabbitMQ hands this worker at once
        await self._channel.set_qos(prefetch_count=concurrency)

        async def on_message(message: AbstractIncomingMessage) -> None:
            # Acknowledged when the block exits normally. If the worker process dies inside it, the
            # job was never acknowledged and RabbitMQ gives it to another worker. If the handler
            # raises, the job is rejected without requeueing so a broken job can't loop forever.
            # TODO: add a dead-letter queue so rejected jobs are kept for inspection, not dropped.
            async with message.process(requeue=False):
                body = json.loads(message.body)
                await handler(body["user_id"], body["job_id"])

        await self._jobs.consume(on_message)


def create_queue() -> JobQueue:
    return RabbitMQQueue()   # the place to choose Pub/Sub or SQS later, e.g. from config
