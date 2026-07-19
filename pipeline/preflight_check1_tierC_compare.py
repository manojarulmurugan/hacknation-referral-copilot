"""Pre-flight Check 1, Tier C compare — agreement between Tier A (vocab/exact-match)
and Tier C (LLM semantic extraction) on the same 25 rows.

NOTE on methodology: Tier C's "one LLM call per facility" was performed directly by
Claude (this session) reading each facility's full concatenated bullet set and
producing structured {orgs_mentioned, cities_mentioned} plus a genuine-contamination
judgment -- no external model API was configured for this diagnostic session. Output
is saved verbatim in tier_c_llm_extraction.json for the user to audit independently.

Output: docs/preflight/tier_c_comparison.json (+ printed summary)
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "preflight"


def main():
    tier_b = {item["unique_id"]: item for item in json.loads((OUT_DIR / "tier_b_sample.json").read_text())}
    tier_c = json.loads((OUT_DIR / "tier_c_llm_extraction.json").read_text())

    rows = []
    for item in tier_c:
        uid = item["unique_id"]
        b = tier_b[uid]
        tier_a_bucket = "obvious" if "obvious" in b["confidence_bucket"] else "borderline"
        rows.append(
            {
                "unique_id": uid,
                "name": item["name"],
                "tier_a_bucket": tier_a_bucket,
                "tier_c_genuine_contamination": item["llm_judged_genuine_contamination"],
            }
        )

    n = len(rows)
    tier_a_obvious = [r for r in rows if r["tier_a_bucket"] == "obvious"]
    tier_a_borderline = [r for r in rows if r["tier_a_bucket"] == "borderline"]
    tier_c_yes = [r for r in rows if r["tier_c_genuine_contamination"]]

    obvious_and_llm_yes = [r for r in tier_a_obvious if r["tier_c_genuine_contamination"]]
    obvious_and_llm_no = [r for r in tier_a_obvious if not r["tier_c_genuine_contamination"]]
    borderline_and_llm_yes = [r for r in tier_a_borderline if r["tier_c_genuine_contamination"]]
    borderline_and_llm_no = [r for r in tier_a_borderline if not r["tier_c_genuine_contamination"]]

    summary = {
        "n_sampled": n,
        "tier_a_obvious_count": len(tier_a_obvious),
        "tier_a_borderline_count": len(tier_a_borderline),
        "tier_c_genuine_contamination_count": len(tier_c_yes),
        "tier_a_obvious_confirmed_by_llm": len(obvious_and_llm_yes),
        "tier_a_obvious_rejected_by_llm": len(obvious_and_llm_no),
        "tier_a_borderline_confirmed_by_llm": len(borderline_and_llm_yes),
        "tier_a_borderline_rejected_by_llm": len(borderline_and_llm_no),
        "precision_of_tier_a_obvious_bucket": round(len(obvious_and_llm_yes) / len(tier_a_obvious), 3) if tier_a_obvious else None,
        "recall_of_tier_a_obvious_bucket_on_llm_confirmed": round(len(obvious_and_llm_yes) / len(tier_c_yes), 3) if tier_c_yes else None,
    }

    (OUT_DIR / "tier_c_comparison.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))

    print("=== Tier C vs Tier A agreement (n=25) ===")
    for k, v in summary.items():
        print(f"{k}: {v}")
    print()
    print("LLM-confirmed genuine contamination cases:")
    for r in tier_c_yes:
        print(f"  - {r['name']} (tier_a_bucket={r['tier_a_bucket']})")
    print()
    print("Tier A 'obvious' rows the LLM judged as NOT genuine contamination (false positives):")
    for r in obvious_and_llm_no:
        print(f"  - {r['name']}")


if __name__ == "__main__":
    main()
