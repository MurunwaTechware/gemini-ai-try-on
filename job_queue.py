"""Job queue: how measurement jobs reach workers, and how "job done" gets back to the web servers.

Two message flows:
  jobs    web server -> one worker    each job is handled by exactly one worker
  events  worker -> every web server  whichever server holds the browser's SSE stream wakes it

JobQueue is the interface main.py and worker.py use. RabbitMQQueue is the only implementation
for now; a cloud provider means one more class with the same methods:
  Google Pub/Sub  jobs: a topic with one shared worker subscription
                  events: a topic with one subscription per web instance
  AWS             jobs: an SQS queue
                  events: an SNS topic fanning out to one SQS queue per web instance
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

JobHandler = Callable[[str], Awaitable[None]]   # called with a job ID; returns once the job is done
DoneHandler = Callable[[str], None]             # called with a job ID when any worker finishes it


class JobQueue(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def publish_job(self, job_id: str) -> None: ...
    async def consume_jobs(self, handler: JobHandler, concurrency: int) -> None: ...
    async def publish_done(self, job_id: str) -> None: ...
    async def subscribe_done(self, handler: DoneHandler) -> None: ...


class RabbitMQQueue:
    JOBS_QUEUE = "measure_jobs"      # durable: queued jobs survive a RabbitMQ restart
    EVENTS_EXCHANGE = "job_events"   # fanout: every bound queue gets a copy of each message
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
        self._events = await self._channel.declare_exchange(self.EVENTS_EXCHANGE, aio_pika.ExchangeType.FANOUT)

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()

    async def publish_job(self, job_id: str) -> None:
        # Persistent, so a job that is queued but not yet picked up survives a broker restart
        message = aio_pika.Message(json.dumps({"job_id": job_id}).encode(),
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
                await handler(json.loads(message.body)["job_id"])

        await self._jobs.consume(on_message)

    async def publish_done(self, job_id: str) -> None:
        await self._events.publish(aio_pika.Message(json.dumps({"job_id": job_id}).encode()), routing_key="")

    async def subscribe_done(self, handler: DoneHandler) -> None:
        # A private, server-named queue for this web instance, deleted when it disconnects.
        # Events sent while no web server is listening are simply missed; that's fine because
        # the SSE stream checks the job folder on disk before it starts waiting.
        queue = await self._channel.declare_queue(exclusive=True)
        await queue.bind(self._events)

        async def on_message(message: AbstractIncomingMessage) -> None:
            handler(json.loads(message.body)["job_id"])

        await queue.consume(on_message, no_ack=True)


def create_queue() -> JobQueue:
    return RabbitMQQueue()   # the place to choose Pub/Sub or SQS later, e.g. from config
