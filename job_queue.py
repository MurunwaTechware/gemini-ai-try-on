"""Job queue: how measurement jobs reach workers. Each job is handled by exactly one worker.

How the waiting page hears back isn't the queue's job: workers update the job's status record in
Firestore (job_status.py), and the web server holding the page's stream listens to that record.

JobQueue is the interface main.py and worker.py use; config.QUEUE_BACKEND picks the implementation:
  RabbitMQQueue   local Docker: one durable queue
  PubSubQueue     the cloud: a topic with one shared worker subscription (infra/terraform/pubsub.tf)
(On AWS it would be an SQS queue: one more class with the same methods.)
"""
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
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
    async def wait_closed(self) -> None: ...   # worker: returns or raises once consuming stops for good


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

    async def wait_closed(self) -> None:
        await asyncio.Future()   # the robust connection reconnects by itself, so this runs until stopped


class PubSubQueue:
    """Google Pub/Sub: the web server publishes each job to a topic, and every worker pulls from the same
    subscription, so each job goes to one worker (infra/terraform/pubsub.tf).

    A job is acknowledged once the handler returns. If the handler raises, the job is handed back (nack):
    Pub/Sub retries it with a growing delay (10 s to 10 min), and after the subscription's max delivery
    attempts moves it to the dead-letter topic, which fires an alert. If a worker dies mid-job, the job
    isn't acknowledged and goes to another worker; worker.py skips the model call if it had finished.

    The client library runs its own threads: each message arrives on one, which runs the handler on the
    worker's event loop and waits for it. While it waits, the library keeps extending the job's lease, so
    a slow measurement isn't handed to a second worker.
    """

    def __init__(self, topic: str = config.PUBSUB_TOPIC, subscription: str = config.PUBSUB_SUBSCRIPTION):
        self._topic = topic
        self._subscription = subscription
        self._publisher = None
        self._subscriber = None
        self._pull = None     # the streaming pull, while consuming
        self._stopped: asyncio.Future | None = None

    async def connect(self) -> None:
        # Clients are made on first use: the web server only publishes and workers only consume.
        # Credentials come from ADC, like the Gemini calls.
        pass

    async def close(self) -> None:
        if self._pull:
            self._pull.cancel()   # stop taking new jobs; jobs in progress are handed back
            await asyncio.to_thread(self._pull.result, timeout=30)
            self._pull = None
        if self._subscriber:
            self._subscriber.close()
        if self._publisher:
            await asyncio.to_thread(self._publisher.stop)   # sends anything still waiting to go

    async def publish_job(self, user_id: str, job_id: str) -> None:
        if self._publisher is None:
            from google.cloud import pubsub_v1
            self._publisher = pubsub_v1.PublisherClient()
        future = self._publisher.publish(self._topic, json.dumps({"user_id": user_id, "job_id": job_id}).encode())
        await asyncio.wrap_future(future)   # raises if Pub/Sub refused it, so the upload says so

    async def consume_jobs(self, handler: JobHandler, concurrency: int) -> None:
        from google.cloud import pubsub_v1
        from google.cloud.pubsub_v1.subscriber.scheduler import ThreadScheduler

        loop = asyncio.get_running_loop()
        self._stopped = loop.create_future()

        def on_message(message) -> None:   # on one of the library's threads
            try:
                body = json.loads(message.data)
                asyncio.run_coroutine_threadsafe(handler(body["user_id"], body["job_id"]), loop).result()
            except Exception:
                log.exception("job message %s failed (delivery attempt %s); handing it back to Pub/Sub",
                              getattr(message, "message_id", "?"), message.delivery_attempt)
                message.nack()
                return
            message.ack()

        def on_stopped(pull) -> None:   # the stream ended: stopped by close(), or an error it can't retry
            error = None if pull.cancelled() else pull.exception()
            def settle():
                if self._stopped.done():
                    return
                if error:
                    self._stopped.set_exception(error)
                else:
                    self._stopped.set_result(None)
            loop.call_soon_threadsafe(settle)

        self._subscriber = pubsub_v1.SubscriberClient()
        self._pull = self._subscriber.subscribe(
            self._subscription,
            on_message,
            # At most `concurrency` jobs at once, each on its own thread
            flow_control=pubsub_v1.types.FlowControl(max_messages=concurrency),
            scheduler=ThreadScheduler(ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="job")),
        )
        self._pull.add_done_callback(on_stopped)

    async def wait_closed(self) -> None:
        if self._stopped is None:
            raise RuntimeError("consume_jobs() first")
        await self._stopped   # raises e.g. PermissionDenied or NotFound for the subscription


def create_queue() -> JobQueue:
    if config.QUEUE_BACKEND == "pubsub":
        return PubSubQueue()
    return RabbitMQQueue()
