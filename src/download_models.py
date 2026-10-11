"""Download and cache required Hugging Face models for offline OSISS usage."""

from __future__ import annotations

import os
import sys

from huggingface_hub import snapshot_download

from config import settings


MODEL_SPECS = [
    {
        "repo_id": "BAAI/bge-m3",
        "local_dir": settings.bge_model_path,
    },
    {
        "repo_id": "deepset/xlm-roberta-large-squad2",
        "local_dir": settings.qa_model_path,
    },
    {
        "repo_id": "BAAI/bge-reranker-v2-m3",
        "local_dir": settings.reranker_model_path,
    },
    {
        # Romanized Bengali/Hindi -> native script (IndicXlit, CTranslate2 build, MIT).
        "repo_id": "Singla0009/all-indic-transliteration",
        "local_dir": os.path.dirname(settings.translit_model_path),
        "allow_patterns": ["README.md", os.path.basename(settings.translit_model_path) + "/*"],
    },
]


def download_model(repo_id: str, local_dir: str, allow_patterns=None) -> None:
    """Download a model snapshot into a deterministic local path."""
    os.makedirs(local_dir, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        local_dir=local_dir,
        local_dir_use_symlinks=False,
        allow_patterns=allow_patterns,
        resume_download=True,
    )
    print(f"[OSISS] Cached '{repo_id}' in '{local_dir}'.")


def main() -> int:
    """Download all required models and return a process exit code."""
    try:
        os.makedirs(settings.models_dir, exist_ok=True)

        for spec in MODEL_SPECS:
            download_model(spec["repo_id"], spec["local_dir"], spec.get("allow_patterns"))

        print("[OSISS] All models downloaded and cached successfully.")
        return 0
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Model download failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
