import json

from smolagents import CodeAgent, LiteLLMModel, InferenceClientModel

from ai_assistant.tools import (
    search_steam_profile,
    query,
    hero_name_to_id,
    item_name_to_id,
    rank_to_badge,
)
from ai_assistant.utils import list_clickhouse_tables, schema

model = {
    "gemini": LiteLLMModel(model_id="gemini/gemini-2.5-flash"),
    "hf": InferenceClientModel(),
}["gemini"]

CONTEXT = {table: schema(table) for table in list_clickhouse_tables()}

agent = CodeAgent(
    model=model,
    tools=[
        hero_name_to_id,
        item_name_to_id,
        rank_to_badge,
        search_steam_profile,
        query,
    ],
    instructions=f"Available Clickhouse Tables: {json.dumps(CONTEXT, indent=2)}",
)

with agent:
    agent.run("How many matches with has johnpyp ever played with an average badge bigger than ascendant?")
