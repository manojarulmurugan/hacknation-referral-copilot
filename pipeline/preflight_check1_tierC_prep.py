"""Pre-flight Check 1, Tier C prep — build per-facility bullet blobs for the
same 25 rows sampled in Tier B, for LLM extraction.

Per the spec: concatenate ALL of a facility's capability+procedure+equipment
bullets (not just the ones Tier A flagged) into one blob per facility, one
LLM call per facility.

Output: docs/preflight/tier_c_input.json
"""

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "facilities_local.parquet"
OUT_DIR = ROOT / "docs" / "preflight"


def parse_json_array(v):
    if v is None:
        return []
    try:
        arr = json.loads(v)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(arr, list):
        return []
    return [b for b in arr if isinstance(b, str) and b.strip()]


def main():
    df = pd.read_parquet(DATA_PATH).drop_duplicates(subset="unique_id", keep="first").set_index("unique_id")
    tier_b = json.loads((OUT_DIR / "tier_b_sample.json").read_text())

    out = []
    for item in tier_b:
        uid = item["unique_id"]
        r = df.loc[uid]
        all_bullets = []
        for field in ["capability", "procedure", "equipment"]:
            all_bullets.extend(parse_json_array(r[field]))
        out.append(
            {
                "unique_id": uid,
                "name": r["name"],
                "address_city": r["address_city"],
                "all_bullets": all_bullets,
                "n_bullets": len(all_bullets),
            }
        )

    (OUT_DIR / "tier_c_input.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"Wrote {len(out)} facility blobs to {OUT_DIR / 'tier_c_input.json'}")
    for item in out:
        print(f"  {item['unique_id']}  name={item['name']!r}  n_bullets={item['n_bullets']}")


if __name__ == "__main__":
    main()
