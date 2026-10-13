"""Explicit, CPU-only preparation of the Chinese semantic retrieval model."""

import argparse
import json
from hashlib import sha256
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, default=Path(__file__).parents[2] / ".build" / "embedding-models")
    parser.add_argument("--model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    from fastembed import TextEmbedding

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    model = TextEmbedding(model_name=args.model, cache_dir=str(args.cache_dir), threads=2, local_files_only=args.offline)
    vector = next(model.embed(["查询订单配送进度"]))
    manifest = {
        "model": args.model, "dimensions": len(vector), "provider": "fastembed/onnx-cpu",
        "files": {str(p.relative_to(args.cache_dir)): sha256(p.read_bytes()).hexdigest()
                  for p in sorted(args.cache_dir.rglob("*")) if p.is_file() and (
                      p.suffix == ".onnx" or p.name in {"config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"}
                  )},
    }
    (args.cache_dir / "resolveai-model-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps({"model": args.model, "dimensions": len(vector), "ready": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
