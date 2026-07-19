"""Pre-flight Check 1, Tier A — full-population deterministic contamination scan.

Flags a facility row if any bullet in capability/procedure/equipment mentions a
city (from the dataset's own address_city vocabulary) other than its own city,
or an organization name (from the dataset's own name vocabulary) other than its
own name. Exact-match / word-boundary substring match only — no fuzzy resolution.

Reproducible: no randomness in this tier. Deterministic given the input parquet.

Outputs (all under docs/preflight/):
    tier_a_flagged_rows.csv       one row per flagged facility (summary)
    tier_a_flagged_bullets.csv    one row per (facility, mismatched bullet)
    tier_a_vocab_cities.json      city vocabulary used
    tier_a_vocab_orgs.json        org-name vocabulary used
"""

import json
from pathlib import Path

import ahocorasick
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "facilities_local.parquet"
OUT_DIR = ROOT / "docs" / "preflight"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BULLET_FIELDS = ["capability", "procedure", "equipment"]


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


def normalize(s: str) -> str:
    return " ".join(s.strip().lower().split())


def build_automaton(terms_by_norm: dict) -> ahocorasick.Automaton:
    A = ahocorasick.Automaton()
    for norm_term, original in terms_by_norm.items():
        A.add_word(norm_term, (norm_term, original))
    A.make_automaton()
    return A


def find_matches(automaton: ahocorasick.Automaton, text_norm: str):
    """Return set of normalized vocab terms found in text_norm, word-boundary safe."""
    hits = set()
    for end_index, (norm_term, _original) in automaton.iter(text_norm):
        start_index = end_index - len(norm_term) + 1
        before_ok = start_index == 0 or not text_norm[start_index - 1].isalnum()
        after_ok = end_index == len(text_norm) - 1 or not text_norm[end_index + 1].isalnum()
        if before_ok and after_ok:
            hits.add(norm_term)
    return hits


def main():
    df = pd.read_parquet(DATA_PATH)
    assert df.shape == (10088, 51), f"unexpected shape {df.shape}"

    # --- Build vocabularies from the dataset itself ---
    city_series = df["address_city"].dropna().map(normalize)
    city_series = city_series[city_series.str.len() > 0]
    city_vocab_norm = {c: c for c in city_series.unique()}  # norm -> norm (cities stored normalized)

    name_series = df["name"].dropna()
    name_norm_map = {}
    for orig in name_series.unique():
        n = normalize(orig)
        if n:
            name_norm_map[n] = orig
    org_vocab_norm = name_norm_map

    print(f"City vocabulary: {len(city_vocab_norm)} distinct normalized city names")
    print(f"Org vocabulary: {len(org_vocab_norm)} distinct normalized org names")

    (OUT_DIR / "tier_a_vocab_cities.json").write_text(
        json.dumps(sorted(city_vocab_norm.keys()), indent=2)
    )
    (OUT_DIR / "tier_a_vocab_orgs.json").write_text(
        json.dumps(sorted(org_vocab_norm.keys()), indent=2)
    )

    city_automaton = build_automaton(city_vocab_norm)
    org_automaton = build_automaton(org_vocab_norm)

    flagged_bullet_rows = []
    flagged_unique_ids = set()

    for row in df.itertuples(index=False):
        own_city_norm = normalize(row.address_city) if isinstance(row.address_city, str) else None
        own_name_norm = normalize(row.name) if isinstance(row.name, str) else None

        for field in BULLET_FIELDS:
            bullets = parse_json_array(getattr(row, field))
            for pos, bullet in enumerate(bullets):
                bullet_norm = normalize(bullet)
                if not bullet_norm:
                    continue

                city_hits = find_matches(city_automaton, bullet_norm)
                foreign_cities = {c for c in city_hits if c != own_city_norm}

                org_hits = find_matches(org_automaton, bullet_norm)
                foreign_orgs = {o for o in org_hits if o != own_name_norm}

                if foreign_cities or foreign_orgs:
                    flagged_unique_ids.add(row.unique_id)
                    flagged_bullet_rows.append(
                        {
                            "unique_id": row.unique_id,
                            "name": row.name,
                            "address_city": row.address_city,
                            "source_field": field,
                            "bullet_position": pos,
                            "bullet_text": bullet,
                            "foreign_cities_mentioned": sorted(foreign_cities),
                            "foreign_orgs_mentioned": sorted(foreign_orgs),
                        }
                    )

    total_rows = len(df)
    n_flagged = len(flagged_unique_ids)
    pct_flagged = 100 * n_flagged / total_rows

    bullets_df = pd.DataFrame(flagged_bullet_rows)

    # --- Diagnostic breakdown: generic single-token org matches ("hospital", "clinic", ...)
    # are known vocab noise (a facility literally named "Hospital" poisons the org vocab).
    # Classify each flagged bullet's strength without altering the core Tier A flag itself.
    def classify(row):
        real_cities = [c for c in row["foreign_cities_mentioned"] if not c.isdigit()]
        has_city = len(real_cities) > 0
        multiword_orgs = [o for o in row["foreign_orgs_mentioned"] if " " in o]
        has_specific_org = len(multiword_orgs) > 0
        if has_city or has_specific_org:
            return "city_or_specific_org_mismatch"
        elif row["foreign_orgs_mentioned"] or row["foreign_cities_mentioned"]:
            return "generic_single_word_or_numeric_noise_only"
        return "unknown"

    bullets_df["flag_strength"] = bullets_df.apply(classify, axis=1)
    bullets_df.to_csv(OUT_DIR / "tier_a_flagged_bullets.csv", index=False)

    strong_ids = set(bullets_df.loc[bullets_df.flag_strength == "city_or_specific_org_mismatch", "unique_id"])
    n_strong = len(strong_ids)
    pct_strong = 100 * n_strong / total_rows

    flagged_summary = (
        bullets_df.groupby("unique_id")
        .agg(
            name=("name", "first"),
            address_city=("address_city", "first"),
            n_flagged_bullets=("bullet_text", "count"),
        )
        .reset_index()
        .sort_values("n_flagged_bullets", ascending=False)
    )
    flagged_summary.to_csv(OUT_DIR / "tier_a_flagged_rows.csv", index=False)

    print()
    print(f"TOTAL ROWS: {total_rows}")
    print(f"FLAGGED ROWS (raw, as specified): {n_flagged} ({pct_flagged:.2f}%)")
    print(f"FLAGGED ROWS (city or multi-word-org mismatch only, excludes generic single-word noise): {n_strong} ({pct_strong:.2f}%)")
    print(f"FLAGGED BULLETS (raw count): {len(bullets_df)}")
    print()
    dup_ids = df["unique_id"][df["unique_id"].duplicated(keep=False)]
    print(f"NOTE: {dup_ids.nunique()} unique_id values appear as exact duplicate row-pairs ({len(dup_ids)} rows total).")
    single_word_orgs = [o for o in org_vocab_norm if " " not in o]
    print(f"NOTE: {len(single_word_orgs)} org-vocab entries are single-token (e.g. facilities literally named a generic word) — these are the main source of the gap between raw and strong flag rates.")
    print()
    print("=== 10 example flagged rows (prioritizing city/multi-word-org mismatches over generic noise) ===")
    df_dedup = df.drop_duplicates(subset="unique_id", keep="first").set_index("unique_id")
    strong_order = [uid for uid in flagged_summary["unique_id"] if uid in strong_ids]
    weak_order = [uid for uid in flagged_summary["unique_id"] if uid not in strong_ids]
    example_uids = (strong_order + weak_order)[:10]
    for uid in example_uids:
        r = df_dedup.loc[uid]
        example_bullet = bullets_df[(bullets_df.unique_id == uid) & (bullets_df.flag_strength == "city_or_specific_org_mismatch")]
        if example_bullet.empty:
            example_bullet = bullets_df[bullets_df.unique_id == uid]
        example_bullet = example_bullet.iloc[0]
        desc = (r["description"] or "")[:200] if isinstance(r["description"], str) else ""
        print(f"- unique_id={uid}")
        print(f"  name={r['name']!r}  address_city={r['address_city']!r}")
        print(f"  description snippet: {desc!r}")
        print(f"  mismatched bullet ({example_bullet['source_field']}[{example_bullet['bullet_position']}]): {example_bullet['bullet_text']!r}")
        print(f"  foreign_cities={example_bullet['foreign_cities_mentioned']} foreign_orgs={example_bullet['foreign_orgs_mentioned']}")
        print()

    # Save a small machine-readable summary too
    summary = {
        "total_rows": total_rows,
        "flagged_rows_raw": n_flagged,
        "pct_flagged_raw": round(pct_flagged, 4),
        "flagged_rows_strong": n_strong,
        "pct_flagged_strong": round(pct_strong, 4),
        "flagged_bullet_count": len(bullets_df),
        "city_vocab_size": len(city_vocab_norm),
        "org_vocab_size": len(org_vocab_norm),
        "single_token_org_vocab_size": len(single_word_orgs),
        "duplicate_unique_id_count": int(dup_ids.nunique()),
    }
    (OUT_DIR / "tier_a_summary.json").write_text(json.dumps(summary, indent=2))
    print("Wrote:", OUT_DIR / "tier_a_summary.json")


if __name__ == "__main__":
    main()
