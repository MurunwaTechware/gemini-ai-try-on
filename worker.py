"""Measurement worker: takes jobs from the queue and runs them. Each job's status record in Firestore
(job_status.py) is what tells the waiting page that it has started and finished.

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

    async def handle(user_id: str, job_id: str) -> None:
        job_dir = jobs.job_path(user_id, job_id)   # raises on a malformed message, so it's rejected
        # A redelivered job (the previous worker died before acknowledging) may already be done;
        # skip the model call rather than paying for it twice, but make sure its status record says so
        if jobs.is_finished(job_dir):
            log.info("job %s already finished, updating its status", job_id)
            await jobs.report_finished(user_id, job_dir)
        else:
            log.info("job %s started", job_id)
            await jobs.run_measurement(user_id, job_dir)   # updates the status record as it goes
            log.info("job %s finished", job_id)

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
