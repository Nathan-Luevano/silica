from __future__ import annotations

import argparse
import json
from pathlib import Path

from pysilica.analyze.g4_corpus import (
    CORPUS_DIR,
    METRICS_FILE,
    build_tier2_records,
    merge_and_compress,
)
from pysilica.analyze.normalize import Normalizer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier1-dir", type=Path, required=True)
    ap.add_argument("--tier2-disasm", type=Path, required=True)
    ap.add_argument("--validity-disagreements", type=int, required=True)
    ap.add_argument("--text-tier-sample-size", type=int, required=True)
    ap.add_argument("--text-tier-population", type=int, required=True)
    args = ap.parse_args()

    normalizer = Normalizer()
    tier2_by_shard, tier2_counts = build_tier2_records(args.tier2_disasm, normalizer)
    tier2_total = sum(tier2_counts.values())

    shards_with_disagreements = merge_and_compress(args.tier1_dir, tier2_by_shard, CORPUS_DIR)

    category_counts = {"VALIDITY": args.validity_disagreements}
    for cat, n in tier2_counts.items():
        category_counts[cat] = category_counts.get(cat, 0) + n

    metrics = {
        "format_version": 1,
        "shards_with_disagreements": shards_with_disagreements,
        "total_disagreements": args.validity_disagreements + tier2_total,
        "category_counts": category_counts,
        "validity_tier_exhaustive": True,
        "validity_disagreements": args.validity_disagreements,
        "text_tier_method": "sampled",
        "text_tier_sample_size": args.text_tier_sample_size,
        "text_tier_population": args.text_tier_population,
    }
    METRICS_FILE.write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
