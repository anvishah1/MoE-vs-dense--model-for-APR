"""Registry of the 5 models in the study: Ollama name -> parameter/architecture
metadata. Shared by the generation script, efficiency scorer, and QI
aggregation, so this is the single source of truth for the numbers that go
into the paper's model table."""

MODELS = {
    "qwen2.5:3b-instruct": {
        "label": "Qwen2.5-3B-Instruct",
        "total_params_b": 3.0,
        "active_params_b": 3.0,
        "architecture": "dense",
        "bracket": "bonus",
    },
    "qwen2.5:7b-instruct": {
        "label": "Qwen2.5-7B-Instruct",
        "total_params_b": 7.0,
        "active_params_b": 7.0,
        "architecture": "dense",
        "bracket": "7B",
    },
    "lly/olmoe-1b-7b-0125-instruct": {
        "label": "OLMoE-1B-7B-0125-Instruct",
        "total_params_b": 7.0,
        "active_params_b": 1.0,
        "architecture": "moe",
        "bracket": "7B",
    },
    "qwen2.5:14b-instruct": {
        "label": "Qwen2.5-14B-Instruct",
        "total_params_b": 14.0,
        "active_params_b": 14.0,
        "architecture": "dense",
        "bracket": "14B",
    },
    "deepseek-coder-v2:16b-lite-instruct-q4_K_M": {
        "label": "DeepSeek-Coder-V2-Lite-Instruct",
        "total_params_b": 16.0,
        "active_params_b": 2.4,
        "architecture": "moe",
        "bracket": "14B",
    },
}
