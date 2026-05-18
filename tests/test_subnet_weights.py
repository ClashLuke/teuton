"""Weight normalization and dry-run publish semantics."""
from __future__ import annotations

from teuton_validator.subnet import BittensorAdapter


def test_normalize_scores_all_zero_returns_zeros_not_equal_split() -> None:
    out = BittensorAdapter.normalize_scores({"miner0": 0.0, "miner1": 0.0})
    assert out == {"miner0": 0.0, "miner1": 0.0}


def test_normalize_scores_single_zero_miner_gets_no_weight() -> None:
    out = BittensorAdapter.normalize_scores({"miner0": 0.0})
    assert out == {"miner0": 0.0}


def test_normalize_scores_positive_split() -> None:
    out = BittensorAdapter.normalize_scores({"miner0": 0.0, "miner1": 3.0})
    assert out["miner0"] == 0.0
    assert abs(out["miner1"] - 1.0) < 1e-9


def test_dry_run_publish_includes_hotkeys_in_extra() -> None:
    adapter = BittensorAdapter(netuid=3, dry_run=True)
    update = adapter.publish_weights({"miner0": 0.0, "miner1": 2.0})
    assert update.extra["hotkeys"] == ["miner0", "miner1"]
    assert update.weights == [0.0, 1.0]
