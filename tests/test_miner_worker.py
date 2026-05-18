"""Miner worker scheduling and heartbeat runtime metrics."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

from teuton_miner.worker import MinerWorker, WorkerConfig
from teuton_runtime.queue import QueueEntry, QueueState


def _worker(*, poll_interval: float = 0.1, poll_interval_idle: float = 1.0) -> MinerWorker:
    bucket = MagicMock()
    config = WorkerConfig(
        netuid=0,
        run_id="run",
        hotkey_ss58="hk",
        worker_id="w0",
        poll_interval=poll_interval,
        poll_interval_idle=poll_interval_idle,
        heartbeat_interval=60.0,
    )
    worker = MinerWorker(bucket=bucket, config=config)
    worker.discovery = MagicMock()
    return worker


def test_poll_sleep_short_when_assigned_work_cached() -> None:
    worker = _worker(poll_interval=0.1, poll_interval_idle=2.0)
    worker._train_queue = QueueState(
        role="train",
        snapshot_unix=int(time.time()),
        snapshot_id=1,
        outstanding=[
            QueueEntry(
                job_id="j1",
                assigned_hotkey="hk",
                assigned_worker="w0",
                manifest_uri="s3://b/m.json",
                grant_uri=None,
                deadline_unix=0,
                attempt=0,
                created_unix=int(time.time()),
            )
        ],
    )
    assert worker._poll_sleep_sec() == 0.1


def test_poll_sleep_idle_when_queue_empty() -> None:
    worker = _worker(poll_interval=0.1, poll_interval_idle=2.0)
    worker._train_queue = QueueState(role="train", snapshot_unix=0, snapshot_id=0, outstanding=[])
    assert worker._poll_sleep_sec() == 2.0


def test_heartbeat_includes_runtime_metrics() -> None:
    worker = _worker()
    worker._train_queue = QueueState(
        role="train",
        snapshot_unix=int(time.time()),
        snapshot_id=1,
        outstanding=[
            QueueEntry(
                job_id="j1",
                assigned_hotkey="hk",
                assigned_worker="w0",
                manifest_uri="s3://b/m.json",
                grant_uri=None,
                deadline_unix=0,
                attempt=0,
                created_unix=int(time.time()) - 5,
            )
        ],
    )
    worker._skip_counts["missing_grant"] = 2
    worker._last_job_id = "prev"
    worker._last_receipt_unix = 99
    worker.heartbeat()
    _args, kwargs = worker.discovery.advertise_worker.call_args
    runtime = kwargs["runtime"]
    assert runtime["assigned_depth"] == 1
    assert runtime["oldest_age_sec"] is not None
    assert runtime["skipped"]["missing_grant"] == 2
    assert runtime["last_job_id"] == "prev"
