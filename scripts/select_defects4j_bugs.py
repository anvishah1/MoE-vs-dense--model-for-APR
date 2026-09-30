"""
Selects 100 Defects4J bugs whose source-level fix (excluding test files) is
at most 3 changed lines, from the 110 such candidates found across the 16
non-Chart projects (Chart excluded: its revisions are plain SVN numbers, not
git hashes, and don't resolve in this git mirror - see project notes).

Selection is a fixed-seed random sample of 100 from the 110 candidates, not
a hand-picked list, so it's reproducible and not cherry-picked.
"""
import csv
import json
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALL_DIFFS_CSV = REPO_ROOT / "data" / "defects4j_all_bug_diffs.csv"
OUT_JSON = REPO_ROOT / "data" / "defects4j_selected_bugs.json"

SEED = 42
TARGET_N = 100
MAX_LINES = 3


def main():
    rows = list(csv.DictReader(open(ALL_DIFFS_CSV)))
    candidates = [
        r for r in rows
        if r["pid"] != "Chart" and int(r["lines_changed"]) in range(1, MAX_LINES + 1)
    ]
    print(f"{len(candidates)} candidates with 1-{MAX_LINES} changed lines (excl. Chart)")

    rng = random.Random(SEED)
    selected = candidates if len(candidates) <= TARGET_N else rng.sample(candidates, TARGET_N)
    selected.sort(key=lambda r: (r["pid"], int(r["bid"])))

    from collections import Counter
    print(f"Selected {len(selected)} bugs. Per-project counts:")
    for pid, n in sorted(Counter(r["pid"] for r in selected).items()):
        print(f"  {pid}: {n}")

    OUT_JSON.write_text(json.dumps(selected, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT_JSON}")


if __name__ == "__main__":
    main()
