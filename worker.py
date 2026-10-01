"""Measurement worker: takes jobs from the queue, runs them, and announces when each is done.

Run alongside the web server when QUEUE_BACKEND=rabbitmq:
    python worker.py
Start more copies (here or on other machines) to process more jobs in parallel.
"""
import asyncio
import logging

import config
import jobs
from job_queue import create_queue

log = logging.getLogger("worker")


async def main() -> None:
    queue = create_queue()
    await queue.connect()

    async def handle(job_id: str) -> None:
        job_dir = config.RUNS_DIR / job_id
        # A redelivered job (the previous worker died before acknowledging) may already be done;
        # skip the model call rather than paying for it twice
        if jobs.is_finished(job_dir):
            log.info("job %s already finished, re-announcing", job_id)
        else:
            log.info("job %s started", job_id)
            await jobs.run_measurement(job_dir)
            log.info("job %s finished", job_id)
        # Announce before the message is acknowledged, so a crash here means a redelivery
        # (handled by the check above) rather than a browser that never hears back
        await queue.publish_done(job_id)

    await queue.consume_jobs(handle, concurrency=config.WORKER_CONCURRENCY)
    log.info("waiting for jobs (up to %d at a time); Ctrl+C to stop", config.WORKER_CONCURRENCY)
    try:
        await asyncio.Future()   # run until stopped
    finally:
        await queue.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
