from demoparser2 import DemoParser
P = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\replays\match730_003824808423086620690_1361276506_405.dem"
p = DemoParser(P)
re = p.parse_event("round_end")
print("cols:", list(re.columns))
print("winner dtype:", re["winner"].dtype if "winner" in re.columns else "NONE")
print("unique winners:", re["winner"].unique().tolist() if "winner" in re.columns else None)
print("unique reasons:", re["reason"].unique().tolist() if "reason" in re.columns else None)
show = [c for c in ["tick", "winner", "reason", "message", "legacy"] if c in re.columns]
print(re[show].head(26).to_string())
