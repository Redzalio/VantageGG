"""Dev check: run the (Stage-3) analytics on a real demo and print the new fields.
Validates rounds_played, UDR (normalized weapons), and trade_opp on real parser output.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics                       # noqa: E402
from demoparser2 import DemoParser     # noqa: E402

DEM = sys.argv[1] if len(sys.argv) > 1 else "uploads/1dc8db2090ec6eb7.dem"
REPLAY = sys.argv[2] if len(sys.argv) > 2 else "cache/sample.json"

replay = None
try:
    replay = json.load(open(REPLAY, encoding="utf-8"))
except Exception as e:
    print("no replay cache, will parse frames:", e)

print(f"parsing analytics on {DEM} ...")
a = analytics.analyze(DemoParser(DEM), replay=replay)
print(f"OK version={a['version']} n_rounds={a['n_rounds']} have_econ={a['have_econ']} "
      f"players={len(a['players'])}")
print(f"{'name':18s} {'rp':>3s} {'K':>3s} {'D':>3s} {'KAST':>5s} {'UDR':>5s} "
      f"{'tradeOpp(c/t/f/%)':>20s}")
for p in a["players"]:
    to = p.get("trade_opp", {})
    print(f"{p['name'][:18]:18s} {p.get('rounds_played','?'):>3} {p['kills']:>3} {p['deaths']:>3} "
          f"{p['kast']:>5} {p['udr']:>5} "
          f"{str((to.get('chances'), to.get('traded'), to.get('failed'), to.get('pct'))):>20}")
# sanity: rounds_played <= n_rounds, KAST in 0..100
bad = [p['name'] for p in a['players']
       if not (0 <= p['kast'] <= 100) or p.get('rounds_played', 0) > a['n_rounds']]
print("SANITY", "FAIL: " + ", ".join(bad) if bad else "ok (KAST 0-100, rounds_played<=n_rounds)")
