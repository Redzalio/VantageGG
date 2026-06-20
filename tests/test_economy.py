"""Tests for the per-round economy verdict on round cards (synthetic; no demo needed).

Covers _econ_verdict directly plus the econ_note/econ_verdict fields that build_round_cards
stamps onto each card: eco loss, anti-force loss, full-lost-to-eco, even, and a no-econ case.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics as an   # noqa: E402


# --- _econ_verdict unit table --------------------------------------------------

def test_econ_verdict_eco_loss():
    # loser ecoed into the winner's full -> expected eco loss.
    v, note = an._econ_verdict("eco", "full", pistol=False)
    assert v == "eco_loss"
    assert note == "Lost an eco"


def test_econ_verdict_anti_force_loss():
    # loser force/light-bought into the winner's full -> anti-force that didn't pay.
    v, note = an._econ_verdict("force", "full", pistol=False)
    assert v == "anti_force_loss"
    assert "Anti-forced" in note
    # light into full is the same story (out-bought but not a true eco).
    v2, note2 = an._econ_verdict("light", "full", pistol=False)
    assert v2 == "anti_force_loss" and note2 == note


def test_econ_verdict_lost_full_v_eco():
    # full-buy beaten by an eco -> NOT an excuse; flag it as notable.
    v, note = an._econ_verdict("full", "eco", pistol=False)
    assert v == "lost_full_v_eco"
    assert note == "Lost a full-buy to an eco"
    # also fires when the winner was on a light buy (still much poorer than a full).
    v2, _ = an._econ_verdict("full", "light", pistol=False)
    assert v2 == "lost_full_v_eco"


def test_econ_verdict_even_has_no_note():
    # both full / both eco / force-vs-full-the-other-way -> comparable, no economy story.
    for lose, win in (("full", "full"), ("eco", "eco"), ("full", "force"), ("force", "force")):
        v, note = an._econ_verdict(lose, win, pistol=False)
        assert v == "even", (lose, win)
        assert note is None, (lose, win)


def test_econ_verdict_no_story_cases():
    # pistols are a fair reset; missing/unknown labels carry no verdict.
    assert an._econ_verdict("full", "eco", pistol=True) == (None, None)
    assert an._econ_verdict(None, "full", pistol=False) == (None, None)
    assert an._econ_verdict("full", None, pistol=False) == (None, None)
    assert an._econ_verdict("unknown", "full", pistol=False) == (None, None)
    assert an._econ_verdict("full", "unknown", pistol=False) == (None, None)


# --- build_round_cards integration --------------------------------------------

def _card_inputs(winner, buy_ct, buy_t, *, pistol=False, anti_eco_ct=False, anti_eco_t=False):
    """Minimal inputs for a single-round build_round_cards call with controlled buys."""
    rounds = [{"num": 1, "start": 0, "freeze_end": 10, "end": 100, "winner": winner, "reason": ""}]
    round_buy = {1: {"ct": buy_ct, "t": buy_t, "pistol": pistol,
                     "anti_eco_ct": anti_eco_ct, "anti_eco_t": anti_eco_t,
                     "mixed_ct": False, "mixed_t": False, "hero_ct": False, "hero_t": False}}
    return dict(rounds=rounds, round_story={}, deaths_by_round={}, team_by_round={1: {}},
                plant_by_round={}, round_buy=round_buy, defuse_rounds=set(), names={}, tickrate=64)


def test_card_eco_loss_field():
    # CT won; T (loser) ecoed into CT full -> eco_loss on the card.
    cards = an.build_round_cards(**_card_inputs("CT", buy_ct="full", buy_t="eco"))
    c = cards[0]
    assert c["econ_verdict"] == "eco_loss"
    assert c["econ_note"] == "Lost an eco"
    # both machine + human fields are present and the human note is short.
    assert len(c["econ_note"]) < 60


def test_card_anti_force_loss_field_and_summary():
    # T won; CT (loser) force-bought into T full -> anti_force_loss; note folded into summary.
    cards = an.build_round_cards(**_card_inputs("T", buy_ct="force", buy_t="full"))
    c = cards[0]
    assert c["econ_verdict"] == "anti_force_loss"
    assert "Anti-forced" in c["econ_note"]
    assert c["econ_note"] in c["summary"]   # appended because no "anti-eco" bit present


def test_card_full_lost_to_eco_field():
    # CT won; T (loser) full-bought and lost to CT eco -> notable lost_full_v_eco.
    cards = an.build_round_cards(**_card_inputs("CT", buy_ct="eco", buy_t="full"))
    c = cards[0]
    assert c["econ_verdict"] == "lost_full_v_eco"
    assert c["econ_note"] == "Lost a full-buy to an eco"


def test_card_even_has_null_note():
    # both full -> even, no note string (None), summary unaffected by econ.
    cards = an.build_round_cards(**_card_inputs("CT", buy_ct="full", buy_t="full"))
    c = cards[0]
    assert c["econ_verdict"] == "even"
    assert c["econ_note"] is None
    assert "—" not in c["summary"]          # nothing folded in


def test_card_no_econ_is_none_and_no_throw():
    # have_econ False path: buys are "unknown" -> econ_note None, econ_verdict None, no error.
    cards = an.build_round_cards(**_card_inputs("CT", buy_ct="unknown", buy_t="unknown"))
    c = cards[0]
    assert c["econ_note"] is None
    assert c["econ_verdict"] is None
    # the rest of the card is still well-formed.
    assert c["round"] == 1 and c["winner"] == "CT" and "summary" in c


def test_card_missing_buy_dict_is_safe():
    # round_buy entirely absent for the round -> rb.get(...) None everywhere, still no throw.
    rounds = [{"num": 1, "start": 0, "freeze_end": 10, "end": 100, "winner": "T", "reason": ""}]
    cards = an.build_round_cards(rounds=rounds, round_story={}, deaths_by_round={},
                                 team_by_round={1: {}}, plant_by_round={}, round_buy={},
                                 defuse_rounds=set(), names={}, tickrate=64)
    c = cards[0]
    assert c["econ_note"] is None and c["econ_verdict"] is None


def test_card_pistol_round_has_no_econ_story():
    # pistol flagged -> fair reset, no economy verdict even with asymmetric labels.
    cards = an.build_round_cards(**_card_inputs("CT", buy_ct="pistol", buy_t="pistol", pistol=True))
    c = cards[0]
    assert c["econ_verdict"] is None and c["econ_note"] is None


def test_card_winner_int_encoding():
    # winner given as int (3=CT) should still resolve the loser correctly via winner_str.
    cards = an.build_round_cards(**_card_inputs(3, buy_ct="full", buy_t="eco"))
    assert cards[0]["econ_verdict"] == "eco_loss"
