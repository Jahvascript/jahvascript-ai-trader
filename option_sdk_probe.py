import ast
from pathlib import Path
from importlib.metadata import distribution

dist = distribution("webull-openapi-python-sdk")
root = Path(dist.locate_file("webull"))

print("\n=== JAHVASCRIPT OPTIONS SDK CHECK ===")
print("SDK version:", dist.version)
print("Mode: OFFLINE SOURCE INSPECTION")
print("Credentials: NOT LOADED\n")

if not root.is_dir():
    raise SystemExit("SDK source directory not found")

# Discover available client interfaces
for relative in (
    "trade/trade_client.py",
    "data/data_client.py"
):
    path = root / relative

    if not path.is_file():
        continue

    tree = ast.parse(path.read_text())

    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue

        if cls.name not in ("TradeClient", "DataClient"):
            continue

        fields = set()

        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            if fn.name != "__init__":
                continue

            for node in ast.walk(fn):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"
                        ):
                            fields.add(target.attr)

        print(f"{cls.name} interfaces:")
        for field in sorted(fields):
            print(" ", field)
        print()

# Discover options-related SDK methods
keywords = (
    "option",
    "contract",
    "expiration",
    "expiry",
    "strike"
)

found = 0

print("--- OPTIONS API METHODS ---")

for path in sorted(root.rglob("*.py")):
    relative = path.relative_to(root).as_posix()

    # Inspect market-data and instrument APIs only
    if not (
        relative.startswith("data/")
        or (
            relative.startswith("trade/")
            and "instrument" in relative
        )
    ):
        continue

    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeError):
        continue

    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue

        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef):
                continue

            name = fn.name.lower()

            if not any(word in name for word in keywords):
                continue

            args = [
                a.arg
                for a in (
                    fn.args.posonlyargs
                    + fn.args.args
                    + fn.args.kwonlyargs
                )
                if a.arg not in ("self", "cls")
            ]

            if fn.args.vararg:
                args.append("*" + fn.args.vararg.arg)

            if fn.args.kwarg:
                args.append("**" + fn.args.kwarg.arg)

            print(
                f"{cls.name}.{fn.name}"
                f"({', '.join(args)})"
            )
            print("  Module:", relative)

            found += 1

print("\nMethods found:", found)
print("SDK INSPECTION COMPLETE")
print("NO NETWORK REQUESTS OR ORDERS SENT")
