"""Memoire vectorielle Maltai.

- Embeddings via le provider (endpoint OpenAI-compatible /v1/embeddings).
- Stockage : vecteurs normalises (norme L2 = 1) packes en float32 dans SQLite.
- Recherche : cosine = simple produit scalaire (vecteurs normalises), en pur
  Python. Suffisant pour une instance perso (quelques milliers de souvenirs).
  Pour passer a l'echelle : migrer vers sqlite-vec (cf. ROADMAP).
"""
from __future__ import annotations

import math
import struct

from core.config import settings
from core import database as db
from src import llm

# Eviter de memoriser des fragments insignifiants.
MIN_CONTENT_CHARS = 12


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"{dim}f", blob))


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


async def embed_one(provider: dict, text: str) -> list[float] | None:
    """Embedding normalise d'un texte, ou None si indisponible."""
    model = provider.get("embed_model") or ""
    if not model:
        return None
    try:
        vecs = await llm.embed(provider["base_url"], provider["api_key"], model, [text])
    except llm.LLMError:
        return None
    if not vecs:
        return None
    return _normalize(vecs[0])


async def remember(
    provider: dict, user_id: str | None, session_id: str | None,
    role: str, content: str,
) -> None:
    """Memorise un message (best-effort : n'echoue jamais bruyamment)."""
    if not settings.MEMORY_ENABLED:
        return
    content = (content or "").strip()
    if len(content) < MIN_CONTENT_CHARS:
        return
    vec = await embed_one(provider, content)
    if vec is None:
        return
    db.add_memory(user_id, session_id, role, content, _pack(vec), len(vec))


async def recall(
    provider: dict, user_id: str | None, query: str,
    exclude_session: str | None = None,
    k: int | None = None,
) -> list[dict]:
    """Retourne les souvenirs les plus pertinents : [{content, role, score}]."""
    if not settings.MEMORY_ENABLED:
        return []
    k = k or settings.MEMORY_TOP_K
    qvec = await embed_one(provider, query)
    if qvec is None:
        return []

    scored = []
    for mid, sess, role, content, blob, dim in db.iter_memories(user_id):
        if exclude_session and sess == exclude_session:
            continue  # deja dans le contexte de la session courante
        if dim != len(qvec):
            continue  # modele d'embedding different : on ignore
        score = _dot(qvec, _unpack(blob, dim))
        if score >= settings.MEMORY_MIN_SCORE:
            scored.append({"content": content, "role": role, "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:k]


def format_context(memories: list[dict]) -> str:
    """Bloc systeme injecte avant la conversation."""
    lines = [
        "Contexte memorise de conversations passees (utilise-le s'il est "
        "pertinent, ignore-le sinon) :"
    ]
    for m in memories:
        who = "Utilisateur" if m["role"] == "user" else "Toi (Maltai)"
        snippet = m["content"].replace("\n", " ")
        if len(snippet) > 400:
            snippet = snippet[:400] + "…"
        lines.append(f"- [{who}] {snippet}")
    return "\n".join(lines)
