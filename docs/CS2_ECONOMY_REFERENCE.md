# CS2 Economy Reference

**Purpose:** the verified economy/weapon/kill-reward facts that back this app's buy-type
classification and economy coaching. Use this as the source of truth; do NOT copy numbers
from random guides without checking here first.

**Checked:** 2026-06-17 (research pass). **Game:** Counter-Strike 2, Competitive / Premier
(MR12), mid-2026 build. Money is in-game dollars.

> Confidence key: **HIGH** = official patch note or unanimous sources; **MED** = well-sourced
> but not patch-confirmed; **LOW / needs-verification** = single/conflicting source.
> When a value is uncertain, the app should say "approx" / "by freeze-end equipment value"
> rather than claim exactness, and code should treat it as a soft threshold.

---

## 1. Match format & baseline (HIGH)

| Fact | Value | Notes |
|---|---|---|
| Format | **MR12**, first to 13 | OT is MR3 from 12-12. (CS:GO was MR15.) Economy mistakes hurt more with fewer rounds. |
| Start money / half | **$800** | Competitive. |
| Money cap | **$16000** | Competitive. |
| OT start money | $10000 (`mp_overtime_startmoney`) | MED — convention, no primary note captured. |

## 2. Round-end team rewards (HIGH)

| Outcome | Reward |
|---|---|
| Win by elimination | **$3250** |
| T win by bomb detonation | **$3500** |
| CT win by defuse | **$3500** |
| CT win by time/defense (no plant) | **$3250** |
| Plant the bomb (the planter) | **+$300** |
| Defuse the bomb (the defuser) | **+$300** |

## 3. Loss-bonus ladder (HIGH)

- Ladder: **$1400 / $1900 / $2400 / $2900 / $3400** (caps at $3400).
- Loss counter **starts at 1** each half → the **pistol-round loser gets $1900**, not $1400.
- Counter **+1 on a loss, −1 on a win** (min 0, max 4). A win does NOT reset it to zero.
- Source: counterstrike.fandom.com/wiki/Money (quotes the Mar 13 2019 + Oct 9 2018 official notes).

## 4. CS2-specific economy changes vs CS:GO (the two that matter)

| Change | Current value | When | Confidence |
|---|---|---|---|
| **T "plant-then-lose" team bonus** (next round) | **$600** (was $800) | Official patch **2024-05-23**: "Reduced the terrorist team award when bomb was planted but defused from $800 to $600." | **HIGH** for $600. ⚠ Many guides (incl. prosettings + the user's notes file) still say **$800 — STALE**. ⚠ Patch text literally says "defused"; community usage generalizes to "T plants and loses → $600" (the timeout-with-plant edge case is MED confidence). |
| **CT +$50 per Terrorist killed** (team-wide, win OR lose) | **+$50 / T killed** | Added **2025-07-16** update. CS2-only; no CS:GO equivalent. Kills after time expires / after bomb explodes don't count. | **HIGH** that it exists; verbatim Valve bullet not captured (needs-verification on exact wording/date). **NOT derivable per-player from our parsed freeze-end data — documented limitation.** |

Other: surviving Terrorists who lose by **timeout without planting get $0** round-end (HIGH).
Short-handed bonus $1000 after 2 rounds with an abandoned player (MED-HIGH).

---

## 5. Weapon / equipment buy prices (current CS2, mid-2026)

Prices are the **same for both teams** except the Molotov (T) / Incendiary (CT) pair.
⚠ flags mark values that **changed from older guides** — verify against these, not stale tables.

### Armor & gear (HIGH)
| Item | $ |
|---|---|
| Kevlar | 650 |
| Kevlar + Helmet | 1000 |
| Defuse Kit | 400 |
| Zeus x27 | 200 |

### Utility (HIGH unless noted)
| Item | $ | Note |
|---|---|---|
| Molotov (T) | 400 | |
| **Incendiary (CT)** | **500** | ⚠ was $600; cut 2024-05-23. |
| Flashbang | 200 | |
| HE grenade | 300 | |
| Smoke | 300 | |
| Decoy | 50 | |

### Pistols (HIGH; defaults are $200 to re-buy)
Glock-18 / USP-S / P2000 **200** · Dual Berettas **300** · P250 **300** ·
Tec-9 / Five-SeveN / CZ75-Auto **500** · Desert Eagle **700** · R8 Revolver **600**

### SMGs
MAC-10 **1050** · MP9 **1250** · **MP7 1400** (⚠ was $1500, cut 2026-01-22) ·
**MP5-SD 1400** (⚠ was $1500) · UMP-45 **1200** · P90 **2350** ·
**PP-Bizon 1300** (⚠ was $1400, cut 2026-01-22)

### Shotguns / LMGs (HIGH)
Nova **1050** · Sawed-Off **1100** (MED) · MAG-7 **1300** · XM1014 **2000** ·
Negev **1700** · M249 **5200**

### Rifles
Galil AR **1800** · **FAMAS 1950** (⚠ was $2050, cut 2025-01-29) · AK-47 **2700** ·
**M4A4 2900** (⚠ was $3100→$3000 (2024-05-23) →$2900 (2025-01-29)) · M4A1-S **2900** ·
SG 553 **3000** · AUG **3300**

### Snipers (HIGH)
SSG 08 **1700** · AWP **4750** · G3SG1 **5000** · SCAR-20 **5000**

---

## 6. Kill rewards (current CS2, HIGH unless noted)

General rule: cheaper/harder weapons pay more (anti-snowball).

| Class / weapon | Kill $ |
|---|---|
| Knife | 1500 |
| Most pistols (incl. **CZ75-Auto**, now 300 — ⚠ was 100 historically) | 300 |
| Rifles, autosnipers (G3SG1/SCAR-20), SSG 08, LMGs (Negev/M249) | 300 |
| SMGs (MP9/MAC-10/MP7/MP5-SD/UMP/Bizon) | 600 |
| **P90** (the SMG exception) | 300 |
| Shotguns (Nova/Sawed-Off/MAG-7) | 900 |
| **XM1014** (the shotgun exception) | 600 ⚠ MED — one source says 300, one says 900; 600 is best-supported |
| AWP | 100 |
| Zeus x27 | 100 |
| Grenade / molotov / incendiary / fire | 300 |

Plus the team mechanic from §4: **every CT gets +$50 per T killed** on top of the kill reward.

---

## 7. Practical buy-type definitions (what the app's labels mean)

These are the concepts the UI labels map onto. The app classifies from **freeze-end
team-average equipment value** (`current_equip_value`), which is **approximate** — it can't
see intent, drops, or whether money was held for next round. Labels are a coaching aid, not
a ground-truth economy ledger.

- **Pistol** — a *scheduled* pistol round only (round 1, the second-half opener, OT half openers).
  Not "any low-equip round."
- **Eco / save** — team intentionally spends ~nothing to bank for a future buy.
- **Light** — upgraded pistols / light armor / SMG-level; no real rifle.
- **Force** — spent most/all money into an *incomplete* buy (deagles, SMGs, Galil/FAMAS, scout,
  thin armor/util, maybe one dropped rifle).
- **Full** — a real rifle/AWP round with armor + useful utility. **CT full costs more than T full**
  (M4 ≈ AK price now, but CT also wants kits + more defensive util) — so the app uses a **higher
  full-buy threshold for CT**. A lone M4 with no kit/util should read as *force*, not full.
- **Bonus** — won a prior round on cheaper guns and kept them instead of upgrading. *Needs
  prior-round carryover to detect — documented limitation, not yet implemented.*
- **Anti-eco** — one side full/strong while the opponent is eco/light.
- **Mixed / broken buy** — large equipment spread inside one team (e.g. two rifles, two pistols,
  one save). Team buy discipline matters more than one player's value.
- **Hero / saved-weapon buy** — one player has a rifle/AWP (often from a save) while the team is
  mostly low. Do **not** call this a team full buy.

### What this app's classifier currently uses / can't use
- **Uses (supported):** per-player + team-average `current_equip_value` and `balance` at freeze-end,
  side (CT/T), per-player equip spread (→ mixed / hero / anti-eco), and an isolated freeze-end
  `has_defuser` parse (→ CT no-kit flag).
- **Cannot reliably do yet (documented limitations):**
  - **low_util** — `current_equip_value` bundles gun+armor+util+kit into one number, so we can't
    cleanly separate "rifle but no nades" without a freeze-end grenade inventory parse. *Needs verification / future.*
  - **bonus** — needs prior-round weapon carryover. *Future.*
  - **CT +$50/T-kill** and exact loss-bonus/next-round money — not reconstructed per player from the
    freeze-end snapshot. The app reasons about *what was on the player*, not the full money ledger.

---

## 8. Sources (fetched 2026-06-17)

Primary (patch notes): HLTV update articles — [Economy/Incendiary 2024-05-23](https://www.hltv.org/news/39059/update-economy-incendiary-vertigo-changes),
[FAMAS/M4A4 Premier S2 2025-01-29](https://www.hltv.org/news/40860/famas-price-reduced-to-1950-mp9-crouching-accuracy-nerfed-in-premier-season-two-update),
[MP7/MP5-SD/Bizon 2026-01-22](https://www.hltv.org/news/43689/anubis-mp7-and-mp5-sd-receive-changes-in-latest-cs2-update).
Reference: [Counter-Strike Wiki — Money](https://counterstrike.fandom.com/wiki/Money) (authoritative on most numbers; its CS:GO/CS2 sections are merged, which caused the stale "$800/$600" hedge).
CT $50/kill + general economy: [Dust2.us](https://www.dust2.us/news/63719/how-does-the-new-cs2-economy-change-affect-the-ct-side),
[cs.money 2026 guide](https://cs.money/blog/esports/ultimate-economy-guide-how-money-works-in-cs2/),
[ProSettings](https://prosettings.net/blog/cs2-economy-guide/) (note: its plant-loss $800 is stale).
Weapon prices cross-checked on gamestatlab + Liquipedia.

## 9. Items still flagged "needs verification"
1. Verbatim Valve bullet for the CT +$50/T-kill change (mechanic + ~2025-07-16 date corroborated; raw text not captured).
2. Whether $600 plant-loss applies to all plant-then-lose outcomes or literally only "defused".
3. OT start money = $10000 for CS2 specifically.
4. XM1014 kill reward (600 vs 300 vs 900 across sources; using 600).
5. Sawed-Off ($1100) and P2000 ($200) rest on fewer confirmations (long-stable; no recent change indicated).
