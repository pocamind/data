#!/usr/bin/env python3

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / ".dist"

# see if it starts with .
EXCLUDE_DIRS = {"."} 


def bundle_dir(target_dir: Path) -> dict:
    result = {}
    for path in sorted(target_dir.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        result[path.stem] = data
    return result


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    targets = [
        d for d in sorted(ROOT.iterdir())
        if d.is_dir() and not any(d.name.startswith(p) for p in EXCLUDE_DIRS)
    ]

    megabundle = {}

    for target_dir in targets:
        bundle = bundle_dir(target_dir)
        if not bundle:
            continue

        out_path = OUT_DIR / f"{target_dir.name}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2, ensure_ascii=False)

        print(f"  {target_dir.name}.json ({len(bundle)} items)")
        megabundle[target_dir.name] = bundle

    all_path = OUT_DIR / "all.json"
    with open(all_path, "w", encoding="utf-8") as f:
        json.dump(megabundle, f, indent=2, ensure_ascii=False)

    total = sum(len(v) for v in megabundle.values())
    print(f"\n  all.json (has {total} total items across {len(megabundle)} categories)")


if __name__ == "__main__":
    main()

