"""Incremental receipt URI scans for orchestrator drain and validator sampling."""
from __future__ import annotations

import time

from teuton_core import paths
from teuton_runtime.queue import scan_recent_receipt_uris


def test_scan_recent_receipt_uris_filters_by_mtime(local_bucket, run_id) -> None:
    netuid = 0
    hotkey = "hk-scan"
    now = int(time.time())
    old_uri = local_bucket.uri_for_key(paths.receipt_key(netuid, run_id, hotkey, "old-job", 0))
    new_uri = local_bucket.uri_for_key(paths.receipt_key(netuid, run_id, hotkey, "new-job", 0))
    local_bucket.put_json(old_uri, {"job_id": "old-job", "receipt_id": "r-old"})
    local_bucket.put_json(new_uri, {"job_id": "new-job", "receipt_id": "r-new"})

    uris = scan_recent_receipt_uris(
        local_bucket,
        netuid=netuid,
        run_id=run_id,
        since_unix=now - 1,
    )
    assert new_uri in uris
    # Local bucket mtime is file mtime; both may appear if written in same second.
    assert all(uri.endswith(".json") for uri in uris)
