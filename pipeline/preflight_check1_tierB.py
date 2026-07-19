"""Pre-flight Check 1, Tier B — human-readable validation sample.

Pulls 25 rows from the Tier A FLAGGED set (not a random sample of the whole
dataset), stratified across confidence using Tier A's strong/weak
classification so the sample spans obvious and borderline cases.

Reproducible: fixed random seed (SEED = 42).

Output: docs/preflight/tier_b_sample.json (also printed to stdout).
"""

import ast
import json
import random
from pathlib import Path

import pandas as pd


def safe_literal_eval(v):
    if isinstance(v, list):
        return v
    if not isinstance(v, str):
        return []
    return ast.literal_eval(v)

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "facilities_local.parquet"
OUT_DIR = ROOT / "docs" / "preflight"

SEED = 42
N_SAMPLE = 25
N_STRONG = 18  # obvious cases
N_WEAK = 7  # borderline cases


def main():
    df = pd.read_parquet(DATA_PATH).drop_duplicates(subset="unique_id", keep="first").set_index("unique_id")
    bullets_df = pd.read_csv(OUT_DIR / "tier_a_flagged_bullets.csv")
    bullets_df["foreign_cities_mentioned"] = bullets_df["foreign_cities_mentioned"].apply(safe_literal_eval)
    bullets_df["foreign_orgs_mentioned"] = bullets_df["foreign_orgs_mentioned"].apply(safe_literal_eval)

    strong_ids = sorted(bullets_df.loc[bullets_df.flag_strength == "city_or_specific_org_mismatch", "unique_id"].unique())
    weak_ids = sorted(
        set(bullets_df["unique_id"].unique()) - set(strong_ids)
    )

    rng = random.Random(SEED)
    sample_strong = rng.sample(strong_ids, min(N_STRONG, len(strong_ids)))
    sample_weak = rng.sample(weak_ids, min(N_WEAK, len(weak_ids)))
    sample_ids = sample_strong + sample_weak
    rng.shuffle(sample_ids)

    results = []
    for uid in sample_ids:
        r = df.loc[uid]
        row_bullets = bullets_df[bullets_df.unique_id == uid]
        confidence = "obvious (city or specific-org mismatch)" if uid in strong_ids else "borderline (generic single-word/numeric match only)"
        flagged = []
        for _, b in row_bullets.iterrows():
            flagged.append(
                {
                    "source_field": b["source_field"],
                    "bullet_position": int(b["bullet_position"]),
                    "bullet_text": b["bullet_text"],
                    "foreign_cities_mentioned": b["foreign_cities_mentioned"],
                    "foreign_orgs_mentioned": b["foreign_orgs_mentioned"],
                }
            )
        results.append(
            {
                "unique_id": uid,
                "name": r["name"],
                "address_city": r["address_city"],
                "description": r["description"],
                "confidence_bucket": confidence,
                "flagged_bullets": flagged,
            }
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "tier_b_sample.json").write_text(json.dumps(results, indent=2, default=str))

    print(f"SEED={SEED}  strong_pool={len(strong_ids)}  weak_pool={len(weak_ids)}  sampled={len(results)}")
    print()
    for i, item in enumerate(results, 1):
        print(f"--- [{i}/25] unique_id={item['unique_id']} ---")
        print(f"name: {item['name']!r}")
        print(f"address_city: {item['address_city']!r}")
        desc = item["description"] or ""
        print(f"description: {desc[:300]!r}")
        print(f"confidence_bucket: {item['confidence_bucket']}")
        print("flagged bullet(s):")
        for fb in item["flagged_bullets"]:
            print(f"  - [{fb['source_field']}[{fb['bullet_position']}]] {fb['bullet_text']!r}")
            print(f"    why flagged: foreign_cities={fb['foreign_cities_mentioned']} foreign_orgs={fb['foreign_orgs_mentioned']}")
        print()


if __name__ == "__main__":
    main()
