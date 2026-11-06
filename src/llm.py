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


# Marqueur expose par /api/health pour verifier la version deployee.
COMPAT_VERSION = "llm-compat-3 (max_completion_tokens, temperature, reasoning, responses)"


class _UseResponses(Exception):
    """Le provider demande l'API /v1/responses pour les outils."""


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
# `max_completion_tokens`), certains n'acceptent que la temperature par defaut,
# et certains refusent les outils avec leur raisonnement par defaut sur
# /chat/completions (il faut alors `reasoning_effort: "none"` avec les outils).
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
            # None = pas encore teste, True = envoyer "none" avec les outils,
            # False = le provider refuse ce parametre (ne plus l'envoyer).
            "reasoning_none_tools": None,
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
        if a["reasoning_none_tools"] is True:
            payload["reasoning_effort"] = "none"
    return payload


def _learn_from_error(base: str, model: str, body: str, has_tools: bool = False) -> str | None:
    """Ajuste les parametres apres une erreur 400.
    Retourne "retry" (reessayer), "responses" (basculer sur /v1/responses) ou None."""
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
        return "retry"
    if a["tokens_param"] == "max_completion_tokens" and param == "max_completion_tokens":
        a["tokens_param"] = "max_tokens"
        return "retry"
    if param == "reasoning_effort" or "reasoning_effort" in msg:
        if "/responses" in msg:
            a["responses_hint"] = True
        # « Function tools with reasoning_effort are not supported ... set
        # reasoning_effort to 'none' » -> 1er essai : raisonnement desactive.
        if has_tools and a["reasoning_none_tools"] is None and "none" in msg:
            a["reasoning_none_tools"] = True
            return "retry"
        # « none » refuse a son tour : on le retire et, si le provider l'a
        # suggere, on passe sur /v1/responses (outils + raisonnement acceptes).
        if a["reasoning_none_tools"] is True:
            a["reasoning_none_tools"] = False
            if has_tools and a.get("responses_hint"):
                a["api"] = "responses"
                return "responses"
            return "retry"
        if has_tools and a.get("responses_hint"):
            a["api"] = "responses"
            return "responses"
        return None
    if a["temperature"] and (param == "temperature" or (
            "temperature" in msg and ("unsupported" in msg or "not support" in msg or "only the default" in msg))):
        a["temperature"] = False
        return "retry"
    return None


def _adapt_summary(base: str, model: str) -> str:
    a = _adapt(base, model)
    bits = [a["tokens_param"]]
    if not a["temperature"]:
        bits.append("sans temperature")
    if a["reasoning_none_tools"] is True:
        bits.append("reasoning_effort=none")
    elif a["reasoning_none_tools"] is False:
        bits.append("reasoning_effort=none refuse")
    if a.get("api") == "responses":
        bits.append("api /responses")
    return ", ".join(bits)


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
            for _attempt in range(5):
                payload = _payload(base, model, messages, temperature, max_tokens, tools)
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code >= 400:
                        last = (await resp.aread()).decode(errors="replace")
                        decision = _learn_from_error(base, model, last, bool(tools)) if resp.status_code == 400 else None
                        if decision == "retry":
                            continue
                        if decision == "responses":
                            raise _UseResponses()
                        raise LLMError(f"Erreur {resp.status_code} : {last[:300]} "
                                       f"[parametres essayes : {_adapt_summary(base, model)}]")
                    async for line in resp.aiter_lines():
                        yield line
                    return
    except httpx.HTTPError as e:
        raise LLMError(f"Connexion au provider impossible : {e}") from e
    raise LLMError(f"Erreur 400 : parametres refuses par le provider : {last[:300]} "
                   f"[parametres essayes : {_adapt_summary(base, model)}]")


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
    if tools and _adapt(_normalize_base(base_url), model).get("api") == "responses":
        async for item in _responses_events(base_url, api_key, model, messages, tools, temperature, max_tokens):
            yield item
        return
    try:
        async for item in _chat_events(base_url, api_key, model, messages, tools, temperature, max_tokens):
            yield item
    except _UseResponses:
        # leve avant tout envoi de donnees (refus 400 du 1er appel) : bascule propre
        async for item in _responses_events(base_url, api_key, model, messages, tools, temperature, max_tokens):
            yield item


async def _chat_events(base_url, api_key, model, messages, tools, temperature, max_tokens):
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


# --- API /v1/responses (outils + raisonnement) ---------------------------------

def _text_of(content) -> str:
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "")


def _to_responses_input(messages: list[dict]) -> list[dict]:
    """Convertit l'historique au format chat/completions vers les items /responses."""
    items: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": str(m.get("tool_call_id") or ""),
                          "output": _text_of(m.get("content"))})
            continue
        if role == "assistant":
            if m.get("content"):
                items.append({"role": "assistant", "content": _text_of(m["content"])})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                items.append({"type": "function_call", "call_id": str(tc.get("id") or fn.get("name") or ""),
                              "name": fn.get("name", ""), "arguments": fn.get("arguments") or "{}"})
            continue
        role = role if role in ("user", "system", "developer") else "user"
        content = m.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    parts.append({"type": "input_text", "text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    iu = part.get("image_url")
                    parts.append({"type": "input_image", "image_url": iu.get("url") if isinstance(iu, dict) else iu})
            items.append({"role": role, "content": parts})
        else:
            items.append({"role": role, "content": str(content or "")})
    return items


def _to_responses_tools(tools: list[dict]) -> list[dict]:
    out = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) else None
        if not fn:
            continue
        out.append({"type": "function", "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                    # strict vaut true par defaut sur /responses : nos schemas ne
                    # sont pas « stricts » (proprietes optionnelles).
                    "strict": False})
    return out


async def _responses_events(base_url, api_key, model, messages, tools, temperature, max_tokens):
    base = _normalize_base(base_url)
    url = base + "/responses"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    a = _adapt(base, model)
    send_temp = a["temperature"]
    send_max = True
    pending: dict[str, dict] = {}
    order: list[str] = []
    got_text = False
    finish_reason: str | None = None
    last = ""
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            for _attempt in range(3):
                payload: dict = {"model": model, "input": _to_responses_input(messages),
                                 "stream": True, "store": False}
                if tools:
                    payload["tools"] = _to_responses_tools(tools)
                if max_tokens and send_max:
                    payload["max_output_tokens"] = int(max_tokens)
                if send_temp and temperature is not None:
                    payload["temperature"] = temperature
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code >= 400:
                        last = (await resp.aread()).decode(errors="replace")
                        low = last.lower()
                        if resp.status_code == 400 and send_temp and "temperature" in low:
                            send_temp = False
                            a["temperature"] = False
                            continue
                        if resp.status_code == 400 and send_max and "max_output_tokens" in low:
                            send_max = False
                            continue
                        raise LLMError(f"Erreur {resp.status_code} (/responses) : {last[:300]}")
                    async for chunk in _chunks(resp.aiter_lines()):
                        typ = chunk.get("type", "")
                        if typ == "response.output_text.delta":
                            piece = chunk.get("delta") or ""
                            if piece:
                                got_text = True
                                yield ("text", piece)
                        elif typ in ("response.output_item.added", "response.output_item.done"):
                            item = chunk.get("item") or {}
                            if item.get("type") == "function_call":
                                key = item.get("id") or item.get("call_id") or str(len(order))
                                if key not in pending:
                                    pending[key] = {"id": "", "type": "function",
                                                    "function": {"name": "", "arguments": ""}}
                                    order.append(key)
                                slot = pending[key]
                                if item.get("call_id"):
                                    slot["id"] = item["call_id"]
                                if item.get("name"):
                                    slot["function"]["name"] = item["name"]
                                if typ.endswith(".done") and item.get("arguments") is not None:
                                    slot["function"]["arguments"] = item["arguments"]
                        elif typ == "response.function_call_arguments.delta":
                            slot = pending.get(chunk.get("item_id", ""))
                            if slot is not None:
                                slot["function"]["arguments"] += chunk.get("delta") or ""
                        elif typ == "response.function_call_arguments.done":
                            slot = pending.get(chunk.get("item_id", ""))
                            if slot is not None and chunk.get("arguments") is not None:
                                slot["function"]["arguments"] = chunk["arguments"]
                        elif typ == "response.completed":
                            finish_reason = "tool_calls" if pending else "stop"
                        elif typ == "response.incomplete":
                            details = (chunk.get("response") or {}).get("incomplete_details") or {}
                            finish_reason = "length" if details.get("reason") == "max_output_tokens" else (details.get("reason") or "incomplete")
                        elif typ in ("response.failed", "error"):
                            err = (chunk.get("response") or {}).get("error") or chunk.get("error") or chunk
                            raise LLMError(f"Erreur /responses : {str(err.get('message') if isinstance(err, dict) else err)[:300]}")
                    break
            else:
                raise LLMError(f"Erreur 400 (/responses) : {last[:300]}")
    except httpx.HTTPError as e:
        raise LLMError(f"Connexion au provider impossible : {e}") from e

    if not got_text and not pending and finish_reason == "length":
        raise _length_error(max_tokens)
    if pending:
        yield ("tool_calls", [pending[k] for k in order])
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
