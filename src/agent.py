"""Boucle agentique Maltai.

Le modele recoit les outils (format OpenAI function calling). Tant qu'il
demande des appels d'outils, on les execute et on reinjecte les resultats.
On s'arrete quand il repond en texte, ou apres MAX_STEPS iterations.

run_agent est un generateur async d'evenements (event, data) que la route
chat transforme en SSE :
  delta        {content}                     - texte du modele
  tool         {name, arguments}             - appel d'outil lance
  tool_result  {name, result}                - resultat (tronque)
  agent_error  {message}
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from core import database as db
from core import plans
from src import context_compress, llm, mcp, tools
from src.prompts import SYSTEM_PROMPT

MAX_STEPS = 8
AGENT_SYSTEM_PROMPT = (
    SYSTEM_PROMPT + " En mode agent, utilise les outils uniquement quand c'est "
    "pertinent (calculs, fichiers du workspace, recherche et lecture web, shell "
    "si disponible). Quand tu as fini, donne ta reponse finale en texte."
)


async def run_agent(
    provider: dict,
    model: str,
    messages: list[dict],
    is_admin: bool,
    temperature: float = 0.7,
    user_id: str | None = None,
    enabled_tools: list[str] | None = None,
    plan: str = "premium",
    max_tokens: int | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    ctx = {"is_admin": is_admin, "provider": provider, "user_id": user_id, "model": model, "plan": plan}
    specs = tools.openai_tool_specs(is_admin, plan)

    # Outils MCP : decouverts au debut de chaque requete agent (best-effort).
    mcp_servers = db.list_mcp_servers(enabled_only=True)
    mcp_dispatch: dict = {}
    if mcp_servers and plans.can_use_tools(plan, is_admin):
        mcp_specs, mcp_dispatch = await mcp.load_mcp_tools(mcp_servers)
        specs = specs + mcp_specs

    # Filtre de l'UI : ne proposer que les outils coches par l'utilisateur.
    if enabled_tools is not None:
        allowed = set(enabled_tools)
        specs = [s for s in specs if s["function"]["name"] in allowed]

    base_messages = [
        m for m in messages
        if not (m.get("role") == "system" and m.get("content") == SYSTEM_PROMPT)
    ]
    convo: list[dict] = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}, *base_messages]

    for _step in range(MAX_STEPS):
        text_parts: list[str] = []
        tool_calls: list[dict] = []

        try:
            async for ev, data in llm.stream_chat_events(
                provider["base_url"], provider["api_key"], model,
                convo, tools=specs, temperature=temperature,
                max_tokens=max_tokens,
            ):
                if ev == "text":
                    text_parts.append(data)
                    yield ("delta", {"content": data})
                elif ev == "tool_calls":
                    tool_calls = data
        except llm.LLMError as e:
            yield ("agent_error", {"message": str(e)})
            return

        text = "".join(text_parts)

        if not tool_calls:
            # Reponse finale en texte : termine.
            return

        # Message assistant avec les tool_calls, requis par le format OpenAI.
        convo.append({
            "role": "assistant",
            "content": text or None,
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"] or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
            yield ("tool", {"name": name, "arguments": raw_args[:400]})
            if not plans.tool_allowed(name, plan, is_admin) and name not in mcp_dispatch:
                result = "Premium requis pour utiliser les outils de l'agent."
            elif enabled_tools is not None and name not in enabled_tools:
                result = "Outil desactive pour cette conversation"
            elif name in mcp_dispatch:
                entry = mcp_dispatch[name]
                try:
                    result = await entry["client"].call_tool(entry["tool"], args)
                except mcp.MCPError as e:
                    result = f"Erreur MCP : {e}"
            else:
                result = await tools.execute_tool(name, args, ctx)
            yield ("tool_result", {"name": name, "result": result[:800]})
            agent_result = context_compress.compress_for_agent(name, result)
            convo.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or name,
                "content": agent_result,
            })

    yield ("agent_error", {"message": f"Limite de {MAX_STEPS} etapes atteinte"})
