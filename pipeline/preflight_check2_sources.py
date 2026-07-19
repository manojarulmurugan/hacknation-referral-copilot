"""Pre-flight Check 2 — distinct source count per facility.

Prints raw examples of source_types / source_ids / source_content_id (format
unknown a priori, per instructions), shows all three side by side for ~10
facilities to surface the ambiguity, then computes a distinct-source-count
histogram using the field that actually represents separate sources.

Reproducible: deterministic, no randomness. Fixed row indices for the
side-by-side ambiguity table (first 10 rows of the dataframe, in file order).

Output: docs/preflight/check2_source_examples.json, check2_histogram.csv
"""

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "facilities_local.parquet"
OUT_DIR = ROOT / "docs" / "preflight"
OUT_DIR.mkdir(parents=True, exist_ok=True)


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

    print("=== 5 raw example values per field (unparsed) ===")
    for col in ["source_types", "source_ids", "source_content_id"]:
        print(f"\n--- {col} (dtype={df[col].dtype}) ---")
        for v in df[col].dropna().head(5):
            print(repr(v)[:200])

    print()
    print("=== side-by-side for first 10 facilities (raw, in file order) ===")
    side_by_side = []
    for row in df.head(10).itertuples(index=False):
        st = parse_json_array(row.source_types)
        sid = parse_json_array(row.source_ids)
        entry = {
            "unique_id": row.unique_id,
            "name": row.name,
            "source_types_raw_len": len(st) if st is not None else None,
            "source_types_distinct": sorted(set(st)) if st is not None else None,
            "source_ids_raw_len": len(sid) if sid is not None else None,
            "source_ids_distinct_count": len(set(sid)) if sid is not None else None,
            "source_content_id": row.source_content_id,
            "source_content_id_type": "scalar (single string, not an array)",
        }
        side_by_side.append(entry)
        print(f"- {row.unique_id}  name={row.name!r}")
        print(f"    source_types: raw_len={entry['source_types_raw_len']} distinct_values={entry['source_types_distinct']}")
        print(f"    source_ids: raw_len={entry['source_ids_raw_len']} distinct_count={entry['source_ids_distinct_count']}")
        print(f"    source_content_id (scalar): {row.source_content_id!r}")

    (OUT_DIR / "check2_source_examples.json").write_text(json.dumps(side_by_side, indent=2, default=str))

    print()
    print("=== Ambiguity note ===")
    print("source_content_id is a SINGLE scalar string per facility (not an array) -- it")
    print("cannot by itself produce a per-facility distinct-source COUNT; it looks like an")
    print("id for the underlying content/document record, not a list of sources.")
    print("source_types is a JSON array but only takes 4 distinct values dataset-wide")
    print("(dynamic/overture/constant/mongo_facility) -- it's a source-TYPE label, not a")
    print("source identifier, and is frequently padded/repeated up to a fixed length (many")
    print("rows cap at exactly 50 entries, which does not reflect genuine bullet/source count).")
    print("source_ids is a JSON array of hash-like ids that DOES repeat within a row (e.g.")
    print("row 0 has 50 raw entries but only 11 distinct ids) -- distinct(source_ids) is the")
    print("field that most plausibly represents genuinely separate underlying sources.")
    print("DECISION: using distinct-count of source_ids per facility as the signal below.")

    def distinct_source_id_count(v):
        arr = parse_json_array(v)
        if arr is None:
            return 0
        return len(set(arr))

    df["distinct_source_count"] = df["source_ids"].map(distinct_source_id_count)

    def bucket(n):
        if n <= 1:
            return "1"
        elif n <= 4:
            return "2-4"
        else:
            return "5+"

    df["bucket"] = df["distinct_source_count"].map(bucket)
    hist = df["bucket"].value_counts().reindex(["1", "2-4", "5+"]).fillna(0).astype(int)
    pct = (100 * hist / len(df)).round(2)

    hist_df = pd.DataFrame({"count": hist, "pct": pct})
    hist_df.to_csv(OUT_DIR / "check2_histogram.csv")

    print()
    print("=== Histogram: distinct source_ids per facility ===")
    print(f"Total facilities: {len(df)}")
    print(hist_df.to_string())
    print()
    print("Raw distribution (percentiles):", df["distinct_source_count"].describe().to_dict())


if __name__ == "__main__":
    main()
