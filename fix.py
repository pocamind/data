import os
import json
from pathlib import Path

# --- configure these ---
DIR = Path("/Users/guanrong/Desktop/data/scaling")   # change this
NEW_KEY = "posture"
NEW_VALUE = ""
# -----------------------

for path in DIR.glob("*.json"):
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        # only add if top-level is an object/dict
        if isinstance(data, dict):
            # optional: skip if key already exists
            if NEW_KEY not in data:
                data[NEW_KEY] = NEW_VALUE

            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            print(f"Skipping non-object JSON in {path}")
    except Exception as e:
        print(f"Failed to process {path}: {e}")
