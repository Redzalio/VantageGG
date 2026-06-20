"""Auto-reparse (.dem-free): analytics_migrations upgrades a stale-but-derivable cached analytics block
in place when served, so old demos pick up new derivable fields without a re-upload or the raw demo."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics_migrations as M   # noqa: E402
from schema import ANALYTICS_VERSION   # noqa: E402


def _v9_cache():
    """A pre-v10 cache: round_cards carry winner + buy_ct/buy_t but NO econ_verdict yet."""
    return {"source_sha1": "x" * 40, "map": "de_dust2", "analytics": {
        "version": 9,
        "round_cards": [
            {"round": 1, "winner": "CT", "buy_ct": "full", "buy_t": "eco", "summary": "."},   # T lost an eco
            {"round": 2, "winner": "T", "buy_ct": "eco", "buy_t": "full", "summary": "."},     # CT lost an eco
            {"round": 3, "winner": 3, "buy_ct": "eco", "buy_t": "full", "summary": "."},       # int winner (CT) -> T lost full to eco
        ],
    }}


def test_migrate_backfills_econ_and_bumps_version():
    data = _v9_cache()
    assert M.migrate(data) is True
    a = data["analytics"]
    assert a["version"] == ANALYTICS_VERSION                  # advanced to current
    for c in a["round_cards"]:
        assert "econ_verdict" in c and "econ_note" in c       # field now present on every card
    assert a["round_cards"][0]["econ_verdict"] == "eco_loss"  # T ecoed into CT's full
    assert a["round_cards"][2]["econ_verdict"] == "lost_full_v_eco"  # int winner handled


def test_migrate_is_idempotent():
    data = _v9_cache()
    assert M.migrate(data) is True
    assert M.migrate(data) is False                           # nothing left to do
    assert data["analytics"]["version"] == ANALYTICS_VERSION


def test_already_current_is_noop():
    data = {"analytics": {"version": ANALYTICS_VERSION, "round_cards": [{"round": 1}]}}
    assert M.migrate(data) is False


def test_safe_on_missing_or_partial():
    assert M.migrate({}) is False
    assert M.migrate({"analytics": None}) is False
    assert M.migrate({"analytics": {"version": 9}}) is False          # no round_cards -> nothing to do
    # a card with no winner/buy data must not throw and yields a null verdict
    data = {"analytics": {"version": 9, "round_cards": [{"round": 1}]}}
    M.migrate(data)
    assert data["analytics"]["round_cards"][0]["econ_verdict"] is None
    assert data["analytics"]["version"] == ANALYTICS_VERSION
