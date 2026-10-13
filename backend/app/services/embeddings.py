import hashlib
import math
import re
import json
from functools import lru_cache
from pathlib import Path

from openai import OpenAI

from app.core.config import Settings, get_settings


class EmbeddingService:
    """LLM-independent embedding providers; semantic inference never downloads implicitly."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.provider = self.settings.embedding_provider
        if self.provider == "auto":
            self.provider = "openai" if self.settings.agent_mode == "openai" else "local"
        self.client = (
            OpenAI(api_key=self.settings.openai_api_key)
            if self.provider == "openai" and self.settings.openai_api_key
            else None
        )
        if self.provider == "openai" and self.client is None:
            raise ValueError("OpenAI embedding provider requires OPENAI_API_KEY")

    @property
    def profile(self) -> str:
        if self.provider == "fastembed":
            manifest = Path(self.settings.embedding_cache_dir) / "resolveai-model-manifest.json"
            if not manifest.is_file():
                raise RuntimeError("Embedding模型清单缺失，请先运行 python -m scripts.prepare_embeddings")
            if json.loads(manifest.read_text(encoding="utf-8"))["model"] != self.settings.semantic_embedding_model:
                raise RuntimeError("Embedding模型清单与配置不一致")
            fingerprint = hashlib.sha256(manifest.read_bytes()).hexdigest()[:16]
            return f"fastembed:{self.settings.semantic_embedding_model}:{fingerprint}"
        if self.provider == "openai":
            return f"openai:{self.settings.openai_embedding_model}:{self.settings.embedding_dimensions}"
        return f"local-hash-v1:{self.settings.embedding_dimensions}"

    def embed(self, text: str) -> list[float]:
        if self.provider == "fastembed":
            return self.embed_documents([text])[0]
        if self.client:
            response = self.client.embeddings.create(
                model=self.settings.openai_embedding_model,
                input=text,
                dimensions=self.settings.embedding_dimensions,
            )
            return response.data[0].embedding
        return self._local_embedding(text)

    def embed_query(self, text: str) -> list[float]:
        if self.provider == "fastembed":
            text = "为这个句子生成表示以用于检索相关文章：" + text
        return self.embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.provider != "fastembed":
            return [self.embed(text) for text in texts]
        model = _semantic_model(self.settings.semantic_embedding_model, self.settings.embedding_cache_dir)
        return [vector.tolist() for vector in model.embed(texts, batch_size=16)]

    def legacy_embed(self, text: str) -> list[float]:
        # Keep the existing fixed-width column compatible with already-running databases.
        return self._local_embedding(text) if self.provider == "fastembed" else self.embed(text)

    def _local_embedding(self, text: str) -> list[float]:
        dimensions = self.settings.embedding_dimensions
        vector = [0.0] * dimensions
        normalized = re.sub(r"\s+", " ", text.lower()).strip()
        words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", normalized)
        chinese = "".join(char for char in normalized if "\u4e00" <= char <= "\u9fff")
        tokens = words + [chinese[i : i + 2] for i in range(max(0, len(chinese) - 1))]
        for token in tokens or [normalized]:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


@lru_cache(maxsize=2)
def _semantic_model(model_name: str, cache_dir: str):
    from fastembed import TextEmbedding

    if not Path(cache_dir).is_dir():
        raise RuntimeError("中文Embedding模型未准备，请先运行 python -m scripts.prepare_embeddings")
    return TextEmbedding(
        model_name=model_name, cache_dir=cache_dir, local_files_only=True, threads=2,
    )
