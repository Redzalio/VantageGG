"""practiceplan.py -- persists which practice-plan items the team has marked done.

The plan items themselves are generated in the UI from a match's coaching output (top focus
areas). Here we only store the done-state, keyed by a stable item id (a hash of the item's
text), so progress is shared across the team and survives restarts -- and the Trends view can
show whether the same issue keeps coming back.
"""
import json
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLAN_PATH = os.environ.get("PRACTICE_FILE") or os.path.join(HERE, "practice.json")


def load_done(path=PLAN_PATH):
    """Return {item_id: True} for items marked done. Missing/corrupt -> {} (never raises)."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return {str(k): True for k, v in d.items() if v} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def set_done(item_id, done, path=PLAN_PATH):
    """Mark a plan item done/undone; atomically persist; return the updated done map."""
    state = load_done(path)
    if done:
        state[str(item_id)] = True
    else:
        state.pop(str(item_id), None)
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(state, out)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return state
