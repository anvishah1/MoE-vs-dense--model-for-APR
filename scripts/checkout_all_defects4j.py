"""One-time batch checkout + metadata caching for all 100 selected Defects4J
bugs. Safe to re-run - checkout_and_cache_metadata skips bugs already done."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from defects4j_harness import checkout_and_cache_metadata

REPO_ROOT = Path(__file__).resolve().parent.parent
bugs = json.loads((REPO_ROOT / "data" / "defects4j_selected_bugs.json").read_text())

ok, failed = 0, []
for i, b in enumerate(bugs, 1):
    pid, bid = b["pid"], b["bid"]
    t0 = time.time()
    try:
        meta = checkout_and_cache_metadata(pid, bid)
        dt = time.time() - t0
        print(f"[{i}/{len(bugs)}] {pid}-{bid} OK ({dt:.1f}s) trigger_tests={len(meta['trigger_tests'])}", flush=True)
        ok += 1
    except Exception as e:
        print(f"[{i}/{len(bugs)}] {pid}-{bid} FAILED: {e}", flush=True)
        failed.append(f"{pid}-{bid}")

print(f"\n{ok}/{len(bugs)} checked out successfully.")
if failed:
    print(f"Failed: {failed}")
