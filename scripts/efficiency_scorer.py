"""
Efficiency dimension: wall-clock generation latency, tokens/sec, and peak VRAM,
all measured on this one machine/engine so every model is directly comparable
- no API-vs-local confound (see project notes on why the original study's
efficiency numbers were meaningless).

Unlike the other 3 dimensions, there's no per-bug "reference" to normalize
against here - efficiency is inherently a cross-model comparison, not a
per-patch correctness check. So this module only captures clean raw
measurements; converting them into a 0-1 QI input happens later in
compute_qi.py once the full dataset's distribution is known (e.g. min-max
or percentile scaling across all 5 models x 140 bugs).

Ollama reports its own internal timing breakdown per request (in nanoseconds):
total_duration, load_duration, prompt_eval_duration, eval_duration,
eval_count. `load_duration` is a one-time model-load cost that shouldn't
penalize whichever bug happens to run first for a given model, so
tokens/sec here is computed from eval_duration (pure generation time), not
total_duration.
"""
import subprocess
import threading
import time

import requests

OLLAMA_HOST = "http://localhost:11434"


def _poll_peak_vram_mb(stop_event: threading.Event, interval_s: float = 0.15) -> list[int]:
    samples = []
    while not stop_event.is_set():
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2,
            )
            samples.append(int(out.stdout.strip().splitlines()[0]))
        except Exception:
            pass
        stop_event.wait(interval_s)
    return samples


CONTEXT_WINDOW = 8192
# Found via a real failure: Ollama's runtime default (n_ctx_slot) is only
# 4096 regardless of a model's max supported context (confirmed in
# server.log), causing hard 400 errors and - worse - silent truncation
# (server.log showed "truncated = 1") on large Defects4J prompts.
# First fix attempt (num_ctx=32768) backfired: llama.cpp pre-allocates the
# FULL KV cache upfront based on num_ctx regardless of actual prompt size,
# and DeepSeek-Coder-V2-Lite was already near its 12GB VRAM ceiling just
# from weights - the 8x larger reservation pushed it into a memory-
# constrained slow path (~2 tok/s instead of ~90, and outright timeouts on
# the largest prompts). The 10 bugs whose prompts actually needed >8192
# were dropped from the Defects4J selection entirely (100 -> 90 bugs) rather
# than chasing a bigger context window further - a documented, deliberate
# scope exclusion, not a hidden gap. 8192 comfortably covers every
# remaining bug's prompt with headroom, without the VRAM blowup.


def generate_with_metrics(model: str, prompt: str, host: str = OLLAMA_HOST, timeout: int = 120) -> dict:
    """Runs one generation and returns the response plus timing/VRAM metrics.
    Uses a background thread to sample nvidia-smi during the request since
    Ollama's API doesn't report memory usage itself."""
    stop_event = threading.Event()
    vram_samples: list[int] = []
    poller = threading.Thread(target=lambda: vram_samples.extend(_poll_peak_vram_mb(stop_event)))
    poller.start()

    wall_start = time.perf_counter()
    try:
        resp = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "options": {"num_ctx": CONTEXT_WINDOW}},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    finally:
        stop_event.set()
        poller.join()
    wall_ms = (time.perf_counter() - wall_start) * 1000

    eval_count = data.get("eval_count", 0)
    eval_duration_ms = data.get("eval_duration", 0) / 1e6
    load_duration_ms = data.get("load_duration", 0) / 1e6
    prompt_eval_count = data.get("prompt_eval_count", 0)
    tokens_per_sec = (eval_count / eval_duration_ms * 1000) if eval_duration_ms > 0 else 0.0

    return {
        "model": model,
        "response": data.get("response", ""),
        "wall_clock_ms": wall_ms,
        "eval_count": eval_count,
        "eval_duration_ms": eval_duration_ms,
        "tokens_per_sec": tokens_per_sec,
        "load_duration_ms": load_duration_ms,
        "prompt_eval_count": prompt_eval_count,
        "peak_vram_mb": max(vram_samples) if vram_samples else None,
        "vram_samples_n": len(vram_samples),
    }


if __name__ == "__main__":
    from models_config import MODELS

    prompt = (
        "Fix the bug in this Java method. Only output the corrected method, no explanation.\n\n"
        "public static int bitcount(int n) {\n"
        "    int count = 0;\n"
        "    while (n != 0) {\n"
        "        n = (n ^ (n - 1));\n"
        "        count++;\n"
        "    }\n"
        "    return count;\n"
        "}"
    )

    print(f"{'model':<32} {'tok/s':>8} {'eval_ms':>10} {'load_ms':>10} {'peak_vram_mb':>13} {'tokens':>7}")
    for model in MODELS:
        try:
            m = generate_with_metrics(model, prompt)
            print(f"{model:<32} {m['tokens_per_sec']:>8.1f} {m['eval_duration_ms']:>10.0f} "
                  f"{m['load_duration_ms']:>10.0f} {str(m['peak_vram_mb']):>13} {m['eval_count']:>7}")
        except Exception as e:
            print(f"{model:<32} ERROR: {e}")
