"""Moteur partage des connecteurs (Telegram, API externe...).

Execute un tour de chat complet (mode simple ou agent, memoire incluse) et
retourne la reponse finale en texte — sans SSE, pour les canaux qui attendent
une reponse entiere (bots de messagerie, webhooks, scripts).
"""
from __future__ import annotations

from core import database as db
from src import agent, llm, memory
from src.prompts import SYSTEM_PROMPT


class ConnectorError(Exception):
    pass


def resolve_provider(provider_id: str | None = None) -> dict:
    """Provider demande, sinon premier provider configure."""
    if provider_id:
        p = db.get_provider(provider_id)
        if p:
            return p
    providers = db.list_providers()
    if not providers:
        raise ConnectorError("Aucun provider configure dans Maltai")
    return providers[0]


async def run_turn(
    session_id: str,
    content: str,
    provider_id: str | None = None,
    model: str | None = None,
    use_agent: bool = False,
    is_admin: bool = False,
    user_id: str | None = None,
) -> str:
    """Tour de chat complet : persiste, rappelle la memoire, repond, memorise."""
    provider = resolve_provider(provider_id)
    model = model or provider.get("model") or ""
    if not model:
        raise ConnectorError("Aucun modele configure sur le provider")

    history = db.list_messages(session_id)
    db.add_message(session_id, "user", content)
    if not history:
        title = content.strip().splitlines()[0][:60] or "Discussion"
        db.rename_session(session_id, title)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history)
    messages.append({"role": "user", "content": content})

    recalled = await memory.recall(provider, user_id, content, exclude_session=session_id)
    if recalled:
        messages = [{"role": "system", "content": memory.format_context(recalled)}, *messages]

    parts: list[str] = []
    if use_agent:
        async for ev, data in agent.run_agent(provider, model, messages, is_admin, user_id=user_id):
            if ev == "delta":
                parts.append(data["content"])
            elif ev == "agent_error":
                raise ConnectorError(data["message"])
    else:
        try:
            async for piece in llm.stream_chat(
                provider["base_url"], provider["api_key"], model, messages
            ):
                parts.append(piece)
        except llm.LLMError as e:
            raise ConnectorError(str(e)) from e

    answer = "".join(parts)
    if answer:
        db.add_message(session_id, "assistant", answer)
    await memory.remember(provider, user_id, session_id, "user", content)
    await memory.remember(provider, user_id, session_id, "assistant", answer)
    return answer
