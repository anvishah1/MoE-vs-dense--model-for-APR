"""
Prompts a model to fix a bug (QuixBugs or Defects4J), extracts the code from
its response, and saves both the reconstructed full-file patch and the full
metrics record (timing/VRAM from efficiency_scorer, raw response for
auditing).

Bug IDs disambiguate the two benchmarks: a bare name like "BITCOUNT" is
QuixBugs; a "Pid-Bid" pair like "Lang-6" is Defects4J (pid is checked
against the known 17 Defects4J project ids, so there's no collision risk
with QuixBugs' underscore-only naming).

For QuixBugs, the whole (small) file is the prompt. For Defects4J, only the
method(s) containing the bug are shown to the model (see defects4j_harness
.get_patch_scope - real files average ~880 lines, too large to have a small
local model retype wholesale) and the model's output is spliced back into
the full original file via reconstruct_full_source() before saving, so the
saved patch_code is always a complete, compilable file either way.

Decoding is greedy (temperature=0) for the primary pass@1 metric - see
project discussion: deterministic and reproducible, with a separate
pass@5-at-temperature>0 subset run planned later as a sampling-robustness
check, not built into this script's default path.
"""
import argparse
import json
import re
from pathlib import Path

from efficiency_scorer import generate_with_metrics
from models_config import MODELS
from quixbugs_harness import QUIXBUGS_DIR, list_bugs as list_quixbugs_bugs
from defects4j_harness import get_patch_scope, reconstruct_full_source

REPO_ROOT = Path(__file__).resolve().parent.parent
PATCHES_DIR = REPO_ROOT / "results" / "patches"
DEFECTS4J_SELECTED = REPO_ROOT / "data" / "defects4j_selected_bugs.json"

DEFECTS4J_PIDS = {
    "Chart", "Cli", "Closure", "Codec", "Collections", "Compress", "Csv", "Gson",
    "JacksonCore", "JacksonDatabind", "JacksonXml", "Jsoup", "JxPath", "Lang", "Math", "Mockito", "Time",
}


def parse_bug_id(bug_id: str) -> tuple[str, str | None, str | None]:
    """Returns (benchmark, pid, bid). benchmark is 'quixbugs' or 'defects4j';
    pid/bid are None for quixbugs."""
    if "-" in bug_id:
        pid, bid = bug_id.split("-", 1)
        if pid in DEFECTS4J_PIDS:
            return "defects4j", pid, bid
    return "quixbugs", None, None


def list_defects4j_bug_ids() -> list[str]:
    bugs = json.loads(DEFECTS4J_SELECTED.read_text(encoding="utf-8"))
    return [f"{b['pid']}-{b['bid']}" for b in bugs]


def list_all_bug_ids() -> list[str]:
    return list_quixbugs_bugs() + list_defects4j_bug_ids()


def safe_model_dirname(model: str) -> str:
    return model.replace(":", "_").replace("/", "__")


def _prompt_text(code: str, whole_file: bool, method_names: list[str] | None = None) -> str:
    if whole_file:
        scope_desc = "The following Java program has exactly one bug. Provide a corrected version of the complete file."
    else:
        names = ", ".join(method_names) if method_names else "the method"
        scope_desc = (
            f"The following Java method(s) ({names}) contain exactly one bug, extracted from a larger file. "
            "Provide a corrected version of this code block only."
        )
    return (
        f"{scope_desc}\n\n"
        "Rules:\n"
        "- Output ONLY the corrected Java code shown below (nothing before or after it).\n"
        "- Do not use markdown code fences.\n"
        "- Do not include any explanation.\n"
        "- Keep method signatures unchanged unless the bug is in the signature itself.\n\n"
        f"{code}"
    )


def build_prompt(bug_id: str) -> dict:
    """Returns {"prompt": str, "scope": dict|None}. scope is None for
    QuixBugs (nothing to splice back); for Defects4J it's the
    get_patch_scope() result, needed later to reconstruct the full file."""
    benchmark, pid, bid = parse_bug_id(bug_id)
    if benchmark == "quixbugs":
        buggy_source = (QUIXBUGS_DIR / "java_programs" / f"{bug_id}.java").read_text(encoding="utf-8")
        return {"prompt": _prompt_text(buggy_source, whole_file=True), "scope": None}

    scope = get_patch_scope(pid, bid)
    prompt = _prompt_text(scope["region_source"], whole_file=scope["whole_file"], method_names=scope["method_names"])
    return {"prompt": prompt, "scope": scope}


_FENCE_RE = re.compile(r"```(?:java)?\s*\n(.*?)```", re.DOTALL)


def extract_java_code(raw_response: str) -> str:
    """Models often wrap output in markdown fences despite being told not
    to. Strip them if present; otherwise assume the whole response is code."""
    m = _FENCE_RE.search(raw_response)
    if m:
        return m.group(1).strip()
    return raw_response.strip()


def generate_patch(model: str, bug_id: str) -> dict:
    benchmark, pid, bid = parse_bug_id(bug_id)
    prompt_info = build_prompt(bug_id)
    metrics = generate_with_metrics(model, prompt_info["prompt"])
    model_code = extract_java_code(metrics["response"])

    if benchmark == "defects4j":
        full_patch_code = reconstruct_full_source(pid, bid, prompt_info["scope"], model_code)
    else:
        full_patch_code = model_code

    return {
        "model": model,
        "bug": bug_id,
        "benchmark": benchmark,
        "patch_code": full_patch_code,
        "model_output_code": model_code,
        "raw_response": metrics["response"],
        "tokens_per_sec": metrics["tokens_per_sec"],
        "eval_count": metrics["eval_count"],
        "eval_duration_ms": metrics["eval_duration_ms"],
        "load_duration_ms": metrics["load_duration_ms"],
        "peak_vram_mb": metrics["peak_vram_mb"],
    }


def save_patch_record(record: dict) -> Path:
    out_dir = PATCHES_DIR / safe_model_dirname(record["model"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{record['bug']}.json"
    out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return out_path


def run_batch(models: list[str], bugs: list[str], retries: int = 2) -> None:
    """retries=2: observed one transient 500 from Ollama's server in a
    50-generation pilot (~2% rate) that succeeded immediately on manual
    retry - cheap insurance at 700-generation scale rather than needing a
    manual sweep for failures afterward."""
    total = len(models) * len(bugs)
    done = 0
    for model in models:
        for bug in bugs:
            done += 1
            print(f"[{done}/{total}] {model} :: {bug} ... ", end="", flush=True)
            last_error = None
            for attempt in range(1 + retries):
                try:
                    record = generate_patch(model, bug)
                    save_patch_record(record)
                    print(f"ok ({record['tokens_per_sec']:.0f} tok/s, {record['eval_count']} tokens)"
                          + (f" [retry {attempt}]" if attempt else ""))
                    break
                except Exception as e:
                    last_error = e
            else:
                print(f"ERROR after {1 + retries} attempts: {last_error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="all", help="comma-separated Ollama model names, or 'all'")
    parser.add_argument("--bugs", default="pilot",
                         help="comma-separated bug ids (e.g. BITCOUNT,Lang-6), 'all', 'quixbugs', 'defects4j', or 'pilot' (first 5 QuixBugs)")
    args = parser.parse_args()

    models = list(MODELS.keys()) if args.models == "all" else args.models.split(",")
    if args.bugs == "all":
        bugs = list_all_bug_ids()
    elif args.bugs == "quixbugs":
        bugs = list_quixbugs_bugs()
    elif args.bugs == "defects4j":
        bugs = list_defects4j_bug_ids()
    elif args.bugs == "pilot":
        bugs = list_quixbugs_bugs()[:5]
    else:
        bugs = args.bugs.split(",")

    print(f"Generating patches: {len(models)} model(s) x {len(bugs)} bug(s)")
    run_batch(models, bugs)
