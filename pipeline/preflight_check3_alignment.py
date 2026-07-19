"""Pre-flight Check 3 — specialties/capability positional alignment.

Parses specialties and capability as JSON arrays, filters to rows where both
are non-null and non-empty, and prints (specialties[i], capability[i]) pairs
for every index i across 30 sampled rows. No judgment applied -- raw pairs
only, for the user to assess.

Reproducible: fixed random seed (SEED = 42) for the 30-row sample.

Output: docs/preflight/check3_pairs.json
"""

import json
import random
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "facilities_local.parquet"
OUT_DIR = ROOT / "docs" / "preflight"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SEED = 42
N_SAMPLE = 30


def parse_json_array(v):
    if v is None:
        return None
    try:
        arr = json.loads(v)
    except (TypeError, json.JSONDecodeError):
        return None
    return arr if isinstance(arr, list) else None


def main():
    df = pd.read_parquet(DATA_PATH).drop_duplicates(subset="unique_id", keep="first")

    df["_spec"] = df["specialties"].map(parse_json_array)
    df["_cap"] = df["capability"].map(parse_json_array)

    def nonempty(arr):
        return arr is not None and len(arr) > 0

    eligible = df[df["_spec"].map(nonempty) & df["_cap"].map(nonempty)]
    print(f"Rows with both specialties and capability non-null/non-empty: {len(eligible)} / {len(df)}")

    rng = random.Random(SEED)
    sample_ids = rng.sample(list(eligible["unique_id"]), min(N_SAMPLE, len(eligible)))

    eligible_indexed = eligible.set_index("unique_id")
    output = []
    for uid in sample_ids:
        row = eligible_indexed.loc[uid]
        spec, cap = row["_spec"], row["_cap"]
        max_len = max(len(spec), len(cap))
        pairs = []
        for i in range(max_len):
            pairs.append(
                {
                    "index": i,
                    "specialties_i": spec[i] if i < len(spec) else None,
                    "capability_i": cap[i] if i < len(cap) else None,
                }
            )
        output.append(
            {
                "unique_id": uid,
                "name": row["name"],
                "len_specialties": len(spec),
                "len_capability": len(cap),
                "pairs": pairs,
            }
        )

    (OUT_DIR / "check3_pairs.json").write_text(json.dumps(output, indent=2, default=str))

    print(f"SEED={SEED}  sampled={len(output)} rows")
    print()
    for item in output:
        print(f"--- unique_id={item['unique_id']}  name={item['name']!r}  len_spec={item['len_specialties']} len_cap={item['len_capability']} ---")
        for p in item["pairs"]:
            print(f"  [{p['index']}] specialties={p['specialties_i']!r}  |  capability={p['capability_i']!r}")
        print()


if __name__ == "__main__":
    main()
