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


# --- Compatibilite des parametres --------------------------------------------
# Les modeles OpenAI recents refusent `max_tokens` (remplace par
# `max_completion_tokens`) et certains n'acceptent que la temperature par defaut.
# On part d'un choix sense selon le provider, puis on s'adapte automatiquement a
# une erreur 400 explicite ; l'adaptation est memorisee par (provider, modele).
_ADAPT: dict[tuple[str, str], dict] = {}
_LENGTH_MSG = (
    "Reponse vide : le modele a atteint la limite de tokens de sortie{limit} avant "
    "de repondre (frequent avec les modeles qui raisonnent). Augmentez la limite, "
    "par ex. MALTAI_MANAGED_OPENAI_MAX_OUTPUT_TOKENS=4000 pour le provider gere."
)


def _is_openai_official(base: str) -> bool:
    b = (base or "").lower()
    return "api.openai.com" in b or ".openai.azure.com" in b


def _adapt(base: str, model: str) -> dict:
    key = (base, model or "")
    if key not in _ADAPT:
        _ADAPT[key] = {
            "tokens_param": "max_completion_tokens" if _is_openai_official(base) else "max_tokens",
            "temperature": True,
        }
    return _ADAPT[key]


def _payload(base: str, model: str, messages: list[dict], temperature: float | None,
             max_tokens: int | None, tools: list[dict] | None = None) -> dict:
    a = _adapt(base, model)
    payload: dict = {"model": model, "messages": messages, "stream": True}
    if a["temperature"] and temperature is not None:
        payload["temperature"] = temperature
    if max_tokens:
        payload[a["tokens_param"]] = int(max_tokens)
    if tools:
        payload["tools"] = tools
    return payload


def _learn_from_error(base: str, model: str, body: str) -> bool:
    """Ajuste les parametres apres une erreur 400. Vrai s'il faut reessayer."""
    a = _adapt(base, model)
    try:
        err = json.loads(body).get("error") or {}
    except (json.JSONDecodeError, AttributeError):
        err = {}
    if not isinstance(err, dict):
        err = {}
    param = str(err.get("param") or "")
    msg = str(err.get("message") or body).lower()
    if a["tokens_param"] == "max_tokens" and (param == "max_tokens" or "max_completion_tokens" in msg):
        a["tokens_param"] = "max_completion_tokens"
        return True
    if a["tokens_param"] == "max_completion_tokens" and param == "max_completion_tokens":
        a["tokens_param"] = "max_tokens"
        return True
    if a["temperature"] and (param == "temperature" or (
            "temperature" in msg and ("unsupported" in msg or "not support" in msg or "only the default" in msg))):
        a["temperature"] = False
        return True
    return False


async def _stream_lines(base_url: str, api_key: str, model: str, messages: list[dict],
                        temperature: float | None, max_tokens: int | None,
                        tools: list[dict] | None = None) -> AsyncIterator[str]:
    """POST /chat/completions en streaming ; yield les lignes SSE brutes.
    Reessaie (avant tout envoi de donnees) si le provider refuse un parametre."""
    base = _normalize_base(base_url)
    url = base + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    last = ""
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            for _attempt in range(3):
                payload = _payload(base, model, messages, temperature, max_tokens, tools)
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code >= 400:
                        last = (await resp.aread()).decode(errors="replace")
                        if resp.status_code == 400 and _learn_from_error(base, model, last):
                            continue
                        raise LLMError(f"Erreur {resp.status_code} : {last[:300]}")
                    async for line in resp.aiter_lines():
                        yield line
                    return
    except httpx.HTTPError as e:
        raise LLMError(f"Connexion au provider impossible : {e}") from e
    raise LLMError(f"Erreur 400 : parametres refuses par le provider : {last[:300]}")


async def _chunks(lines: AsyncIterator[str]) -> AsyncIterator[dict]:
    async for line in lines:
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(chunk, dict):
            yield chunk


def _length_error(max_tokens: int | None) -> LLMError:
    return LLMError(_LENGTH_MSG.format(limit=f" ({int(max_tokens)})" if max_tokens else ""))


async def stream_chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    """Yield les morceaux de texte au fur et a mesure (SSE OpenAI)."""
    got_text = False
    finish_reason: str | None = None
    async for chunk in _chunks(_stream_lines(base_url, api_key, model, messages, temperature, max_tokens)):
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        finish_reason = choice.get("finish_reason") or finish_reason
        piece = (choice.get("delta") or {}).get("content")
        if piece:
            got_text = True
            yield piece
    if not got_text and finish_reason == "length":
        raise _length_error(max_tokens)


async def stream_chat_events(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> AsyncIterator[tuple[str, object]]:
    """Stream avec support du function calling OpenAI.

    Yield des tuples :
      ("text", str)          - morceau de texte
      ("tool_calls", list)   - appels d'outils completes (en fin de tour)
      ("finish", str|None)   - finish_reason
    """
    pending: dict[int, dict] = {}  # accumulation des tool_calls par index
    finish_reason: str | None = None
    got_text = False

    async for chunk in _chunks(_stream_lines(base_url, api_key, model, messages, temperature, max_tokens, tools)):
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]
        delta = choice.get("delta") or {}
        piece = delta.get("content")
        if piece:
            got_text = True
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

    if not got_text and not pending and finish_reason == "length":
        raise _length_error(max_tokens)
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
    messages: list[dict], temperature: float = 0.3, max_tokens: int | None = None,
) -> str:
    """Completion non-streamee (joint le stream) — pour les outils internes."""
    parts: list[str] = []
    async for piece in stream_chat(base_url, api_key, model, messages, temperature, max_tokens=max_tokens):
        parts.append(piece)
    return "".join(parts)
