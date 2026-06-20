"""Player-vs-player (and self-period) comparison helpers for VantageGG.

Presentation logic over ALREADY-aggregated stats. The numbers are produced by
``db.player_trends`` (which averages a player's per-match rows); this module only
diffs two stat dicts and decides, per metric, who leads. It does NOT touch the
DB except via ``db.player_trends`` in :func:`compare_from_trends`.

The comparable metrics mirror the exact keys ``db.player_trends`` returns in its
``averages`` dict (and in every ``series`` entry): hltv, adr, kast, open_wr,
traded_pct, udr -- plus kd. See ``matchindex._STATS`` / ``_PLAYER_FIELDS``.

Design rules:
* ``build_comparison`` is a PURE function and never raises -- bad/missing values
  become a ``"na"`` metric (no winner) rather than an error.
* ``better`` is honored generically: a descriptor with ``better="low"`` flips the
  winner test, so this works unchanged if a "lower is better" stat (e.g. deaths)
  is ever added to the trend output.
"""

# Metric descriptors. `key` MUST match a key in db.player_trends()'s averages dict.
# `better`: "high" => larger value wins; "low" => smaller value wins.
# `unit`: ""=raw number, "%"=percentage, "rating"=HLTV-style rating.
METRICS = [
    {"key": "hltv",       "label": "Rating",        "better": "high", "unit": "rating"},
    {"key": "adr",        "label": "ADR",           "better": "high", "unit": ""},
    {"key": "kast",       "label": "KAST",          "better": "high", "unit": "%"},
    {"key": "kd",         "label": "K/D",           "better": "high", "unit": ""},
    {"key": "open_wr",    "label": "Opening WR",    "better": "high", "unit": "%"},
    {"key": "traded_pct", "label": "Traded %",      "better": "high", "unit": "%"},
    {"key": "udr",        "label": "Util Dmg/Rd",   "better": "high", "unit": ""},
]

# How many decimals to render per metric (matches matchindex._round: rating-ish -> 2, else 1).
_PRECISION = {"hltv": 2, "kd": 2}


def _coerce(v):
    """Return v as a float, or None if missing / not numeric (NaN counts as missing)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _round_metric(key, val):
    if val is None:
        return None
    return round(val, _PRECISION.get(key, 1))


def _winner(a_val, b_val, better):
    """'a' | 'b' | 'tie' | 'na' for one metric, honoring better=high/low."""
    if a_val is None or b_val is None:
        return "na"
    if a_val == b_val:
        return "tie"
    a_leads = a_val > b_val if better == "high" else a_val < b_val
    return "a" if a_leads else "b"


def _metric_label(m):
    return m.get("label") or m.get("key")


def build_comparison(stats_a, stats_b, *, label_a=None, label_b=None,
                     metrics=None):
    """Compare two already-aggregated stat dicts. PURE; never raises.

    `stats_a` / `stats_b` are dicts shaped like db.player_trends()'s ``averages``
    (e.g. {"hltv": 1.12, "adr": 81.4, ...}); extra keys are ignored, missing keys
    are treated as "na". You may pass full player_trends() results too -- a
    convenience layer in :func:`compare_from_trends` extracts ``averages`` first.

    `label_a` / `label_b` are display names (fall back to "Player A"/"Player B").
    `metrics` overrides the default :data:`METRICS` descriptor list (used in tests
    to exercise the ``better="low"`` path).

    Returns::

        {
          "a": {"label": str, "matches": int|None},
          "b": {"label": str, "matches": int|None},
          "metrics": [
            {key, label, unit, better, a_val, b_val, delta, winner},
            ...
          ],
          "summary": "<one-line who-leads-where>",
        }

    ``a_val``/``b_val`` are rounded floats or None. ``delta = a_val - b_val``
    (rounded; None if either side is missing). ``winner`` in {"a","b","tie","na"}.
    """
    stats_a = stats_a if isinstance(stats_a, dict) else {}
    stats_b = stats_b if isinstance(stats_b, dict) else {}
    descriptors = metrics if metrics is not None else METRICS

    la = label_a or "Player A"
    lb = label_b or "Player B"

    out_metrics = []
    a_wins = b_wins = 0
    for m in descriptors:
        key = m.get("key")
        better = m.get("better", "high")
        a_val = _round_metric(key, _coerce(stats_a.get(key)))
        b_val = _round_metric(key, _coerce(stats_b.get(key)))
        win = _winner(a_val, b_val, better)
        if win == "a":
            a_wins += 1
        elif win == "b":
            b_wins += 1
        delta = None
        if a_val is not None and b_val is not None:
            delta = _round_metric(key, a_val - b_val)
        out_metrics.append({
            "key": key,
            "label": _metric_label(m),
            "unit": m.get("unit", ""),
            "better": better,
            "a_val": a_val,
            "b_val": b_val,
            "delta": delta,
            "winner": win,
        })

    summary = _summarize(la, lb, out_metrics, a_wins, b_wins)
    return {
        "a": {"label": la, "matches": _coerce_int(stats_a.get("n_matches"))},
        "b": {"label": lb, "matches": _coerce_int(stats_b.get("n_matches"))},
        "metrics": out_metrics,
        "summary": summary,
    }


def _coerce_int(v):
    f = _coerce(v)
    return int(f) if f is not None else None


def _summarize(la, lb, metrics, a_wins, b_wins):
    """One-line, human plain-English summary of who leads where. Never empty."""
    comparable = [m for m in metrics if m["winner"] in ("a", "b", "tie")]
    if not comparable:
        return "Not enough overlapping stats to compare."
    # Headline: overall edge by metric-win count.
    if a_wins > b_wins:
        head = "%s leads (%d-%d metrics)" % (la, a_wins, b_wins)
    elif b_wins > a_wins:
        head = "%s leads (%d-%d metrics)" % (lb, b_wins, a_wins)
    else:
        head = "Dead even (%d-%d metrics)" % (a_wins, b_wins)
    # Detail: each player's single biggest edge (largest normalized gap they win).
    a_edge = _biggest_edge(metrics, "a")
    b_edge = _biggest_edge(metrics, "b")
    bits = []
    if a_edge:
        bits.append("%s's edge: %s" % (la, a_edge))
    if b_edge:
        bits.append("%s's edge: %s" % (lb, b_edge))
    return head + ("; " + "; ".join(bits) if bits else "")


def _biggest_edge(metrics, who):
    """Label of the metric where `who` has the largest relative lead, or None."""
    best, best_score = None, -1.0
    for m in metrics:
        if m["winner"] != who or m["delta"] is None:
            continue
        gap = abs(m["delta"])
        base = abs(m["a_val"]) + abs(m["b_val"]) or 1.0
        score = gap / base  # normalize so % stats don't always dominate raw ones
        if score > best_score:
            best, best_score = m["label"], score
    return best


def compare_from_trends(con, sid_a, sid_b, scope=None):
    """DB-backed: pull each player's averaged stats via db.player_trends and compare.

    `con` is an open sqlite connection (db.connect()); `scope` is the same scope
    object the routes pass through to player_trends (None = open/local mode).
    Reuses the caller's connection so it joins the request's transaction/visibility.
    """
    import db  # local import: keeps compare.py importable without a DB present

    ta = db.player_trends(sid_a, scope=scope, con=con)
    tb = db.player_trends(sid_b, scope=scope, con=con)
    return build_comparison(
        _trend_stats(ta),
        _trend_stats(tb),
        label_a=ta.get("name") or str(sid_a),
        label_b=tb.get("name") or str(sid_b),
    )


def _trend_stats(trends):
    """Flatten a player_trends() result into a single stat dict build_comparison reads.

    The metric values live in trends["averages"]; n_matches lives at the top level,
    so fold it in (build_comparison surfaces it as the per-player match count)."""
    stats = dict(trends.get("averages") or {})
    stats["n_matches"] = trends.get("n_matches")
    return stats


def compare_self_periods(series, split_n=5, *, label_a="Recent", label_b="Prior"):
    """Compare ONE player's last N matches vs the previous N (form / trajectory).

    `series` is db.player_trends()[...]["series"] -- a list of per-match stat dicts
    ordered OLD->NEW (as player_trends returns them). Splits off the last `split_n`
    as "recent" and the `split_n` before that as "prior", averages each window over
    the METRICS keys, and runs build_comparison. Returns the same shape as
    build_comparison, plus an ``n`` block describing the window sizes. Returns
    ``{"available": False, ...}`` when there isn't enough history to form two
    windows (need >= split_n+1 matches). Never raises.
    """
    series = series if isinstance(series, list) else []
    n = len(series)
    if split_n < 1 or n < split_n + 1:
        return {
            "available": False,
            "reason": "need >= %d matches (have %d)" % (split_n + 1, n),
            "metrics": [],
            "summary": "Not enough matches yet to compare recent vs prior form.",
        }
    recent = series[-split_n:]
    prior = series[-2 * split_n:-split_n] if n >= 2 * split_n else series[:-split_n]
    rec_avg = _avg_window(recent)
    pri_avg = _avg_window(prior)
    rec_avg["n_matches"] = len(recent)
    pri_avg["n_matches"] = len(prior)
    cmp = build_comparison(rec_avg, pri_avg, label_a=label_a, label_b=label_b)
    cmp["available"] = True
    cmp["n"] = {"recent": len(recent), "prior": len(prior)}
    return cmp


def _avg_window(window):
    """Average each METRICS key across a list of per-match stat dicts (missing -> skipped)."""
    out = {}
    for m in METRICS:
        key = m["key"]
        vals = [v for v in (_coerce(row.get(key)) for row in window) if v is not None]
        out[key] = _round_metric(key, sum(vals) / len(vals)) if vals else None
    return out
