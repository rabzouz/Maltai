"""Client LLM compatible OpenAI.

Un seul client couvre Ollama, vLLM, llama.cpp, OpenRouter, OpenAI... il suffit
de changer base_url + api_key + model. Streaming via l'API /chat/completions.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx


class LLMError(Exception):
    pass


def normalize_base(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if base and "://" not in base:
        base = "http://" + base
    # Ollama natif expose /v1 ; on tolere une URL sans /v1.
    if base.endswith("/v1"):
        return base
    return base + "/v1"


_normalize_base = normalize_base


async def list_models(base_url: str, api_key: str) -> list[str]:
    url = _normalize_base(base_url) + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Impossible de lister les modeles : {e}") from e
    data = r.json()
    return [m.get("id", "") for m in data.get("data", []) if m.get("id")]


async def stream_chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
) -> AsyncIterator[str]:
    """Yield les morceaux de texte au fur et a mesure (SSE OpenAI)."""
    url = _normalize_base(base_url) + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise LLMError(
                        f"Erreur {resp.status_code} : {body.decode(errors='replace')[:300]}"
                    )
                async for chunk_text in _iter_sse(resp):
                    yield chunk_text
    except httpx.HTTPError as e:
        raise LLMError(f"Connexion au provider impossible : {e}") from e


async def stream_chat_events(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = 0.7,
) -> AsyncIterator[tuple[str, object]]:
    """Stream avec support du function calling OpenAI.

    Yield des tuples :
      ("text", str)          - morceau de texte
      ("tool_calls", list)   - appels d'outils completes (en fin de tour)
      ("finish", str|None)   - finish_reason
    """
    url = _normalize_base(base_url) + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools

    pending: dict[int, dict] = {}  # accumulation des tool_calls par index
    finish_reason: str | None = None

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise LLMError(
                        f"Erreur {resp.status_code} : {body.decode(errors='replace')[:300]}"
                    )
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        yield ("text", piece)
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = pending.setdefault(
                            idx, {"id": "", "type": "function",
                                  "function": {"name": "", "arguments": ""}}
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
    except httpx.HTTPError as e:
        raise LLMError(f"Connexion au provider impossible : {e}") from e

    if pending:
        calls = [pending[i] for i in sorted(pending)]
        yield ("tool_calls", calls)
    yield ("finish", finish_reason)


async def _iter_sse(resp) -> AsyncIterator[str]:
    """Parse les lignes SSE OpenAI et yield le contenu texte des deltas."""
    async for line in resp.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        piece = delta.get("content")
        if piece:
            yield piece


async def embed(base_url: str, api_key: str, model: str, texts: list[str]) -> list[list[float]]:
    """Appelle /v1/embeddings (format OpenAI). Retourne un vecteur par texte."""
    url = _normalize_base(base_url) + "/embeddings"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "input": texts}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Embeddings indisponibles : {e}") from e
    data = r.json().get("data", [])
    data.sort(key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in data]


async def complete(
    base_url: str, api_key: str, model: str,
    messages: list[dict], temperature: float = 0.3,
) -> str:
    """Completion non-streamee (joint le stream) — pour les outils internes."""
    parts: list[str] = []
    async for piece in stream_chat(base_url, api_key, model, messages, temperature):
        parts.append(piece)
    return "".join(parts)
