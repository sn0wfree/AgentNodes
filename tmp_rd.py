import io

for name in ["progress", "resilience"]:
    p = rf"D:\Github\AgentNodes\src\agentnodes\kernel\agent\strategy\steps\{name}.py"
    print(f"===== {name}.py =====")
    print(open(p, encoding="utf-8", errors="replace").read())
