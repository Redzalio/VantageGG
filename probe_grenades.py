from demoparser2 import DemoParser
P = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\replays\match730_003824808423086620690_1361276506_405.dem"
g = DemoParser(P).parse_grenades()
print("columns:", list(g.columns))
print(g.head(10).to_string())
