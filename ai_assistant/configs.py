import logging
import os

from smolagents import LiteLLMModel, InferenceClientModel, ApiModel

from ai_assistant import utils
from ai_assistant.message_store import MessageStore, RedisMessageStore, MemoryMessageStore
from ai_assistant.utils import formatted_table_schema, list_clickhouse_tables

LOGGER = logging.getLogger(__name__)

DO_RELEVANCY_CHECK = os.environ.get("DO_RELEVANCY_CHECK", "false").lower() in ("true", "1", "yes")

AUTHORIZED_IMPORTS = [
    "numpy",
    "pandas",
    "requests",
    "matplotlib",
    "matplotlib.pyplot",
    "seaborn",
    "string",
    "random",
    "PIL",
]
TABLES_CONTEXT = "\n\n".join(formatted_table_schema(table) for table in list_clickhouse_tables())
AGENT_INSTRUCTIONS = f"""
All Heroes: {", ".join(utils.list_heroes())}

Available Clickhouse Tables:
{TABLES_CONTEXT}

You can use the following imports to generate plots: {AUTHORIZED_IMPORTS}
Plots must be saved in directory `plots` as **png** files.
After you generated a plot, you must include the filename of the plot (including the `plots/` prefix) in your final answer.
Add a random string into the filename to avoid overwriting existing plots.
For example: "Plot saved to plots/my_plot_asojo30023.png"

Format the final answer as simple string.
"""

MODEL_CONFIGS = {
    "gemini-flash-lite": lambda: LiteLLMModel(model_id="gemini/gemini-3-flash-lite-preview"),
    "gemini-flash": lambda: LiteLLMModel(model_id="gemini/gemini-3-flash-preview"),
    "gemini-pro": lambda: LiteLLMModel(model_id="gemini/gemini-3-pro-preview"),
    "ollama": lambda: LiteLLMModel(model_id="ollama/qwen2.5-coder:14b"),
    "hf": lambda: InferenceClientModel(),
}
DEFAULT_MODEL = "gemini-3-flash-preview"

REPLAY = [
    'event: agentStep\ndata: {"type": "action_output", "data": "ActionOutput(output=None, is_final_answer=False)"}\n\n',
    'event: agentStep\ndata: {"type": "action", "data": [{"role": "assistant", "content": [{"type": "text", "text": "<code>\\njohnpyp_account_id = search_steam_profile(name_or_id=\\"johnpyp\\")\\nprint(f\\"Johnpyp\'s account ID: {johnpyp_account_id}\\")\\n</code>"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-call", "content": [{"type": "text", "text": "Calling tools:\\n[{\'id\': \'call_1\', \'type\': \'function\', \'function\': {\'name\': \'python_interpreter\', \'arguments\': \'johnpyp_account_id = search_steam_profile(name_or_id=\\"johnpyp\\")\\\\nprint(f\\"Johnpyp\\\\\'s account ID: {johnpyp_account_id}\\")\'}}]"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-response", "content": [{"type": "text", "text": "Observation:\\nExecution logs:\\nJohnpyp\'s account ID: 127331261\\nLast output from code snippet:\\nNone"}], "tool_calls": null, "raw": null, "token_usage": null}]}\n\n',
    'event: agentStep\ndata: {"type": "action_output", "data": "ActionOutput(output=None, is_final_answer=False)"}\n\n',
    'event: agentStep\ndata: {"type": "action", "data": [{"role": "assistant", "content": [{"type": "text", "text": "Thought: I have Johnpyp\'s account ID. Next, I need to find the badge ID for \'Ascendant\' so I can filter matches where the average badge was greater than it. I will use the `rank_to_badge` tool for this.\\n<code>\\nascendant_badge_id = rank_to_badge(rank_name=\\"Ascendant\\")\\nprint(f\\"Ascendant badge ID: {ascendant_badge_id}\\")\\n</code>"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-call", "content": [{"type": "text", "text": "Calling tools:\\n[{\'id\': \'call_2\', \'type\': \'function\', \'function\': {\'name\': \'python_interpreter\', \'arguments\': \'ascendant_badge_id = rank_to_badge(rank_name=\\"Ascendant\\")\\\\nprint(f\\"Ascendant badge ID: {ascendant_badge_id}\\")\'}}]"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-response", "content": [{"type": "text", "text": "Observation:\\nExecution logs:\\nAscendant badge ID: 100\\nLast output from code snippet:\\nNone"}], "tool_calls": null, "raw": null, "token_usage": null}]}\n\n',
    'event: agentStep\ndata: {"type": "action_output", "data": "ActionOutput(output=None, is_final_answer=False)"}\n\n',
    'event: agentStep\ndata: {"type": "action", "data": [{"role": "assistant", "content": [{"type": "text", "text": "Thought: I have both Johnpyp\'s account ID and the Ascendant badge ID. Now I need to query the `match_player` and `match_info` tables to find matches where Johnpyp participated and the `average_badge_team0` or `average_badge_team1` was greater than the Ascendant badge ID. I will count the number of such unique matches.\\nI need to join `match_player` with `match_info` on `match_id`. Then filter by `account_id` and check the average badge of both teams.\\n\\nGiven that `average_badge_team0` and `average_badge_team1` are nullable, I should handle `NULL` values by considering them as 0 or simply ignore them in the comparison if it\'s more appropriate, but usually, `NULL` values are excluded from comparisons. In this case, comparing `NULL` with `> 100` would evaluate to `NULL`, so records with `NULL` average badges won\'t be counted.\\n\\nI will use a SQL query to count the distinct matches where Johnpyp played and either team\'s average badge was greater than 100.\\n<code>\\nquery = f\\"\\"\\"\\nSELECT\\n    COUNT(DISTINCT mp.match_id)\\nFROM\\n    match_player AS mp\\nJOIN\\n    match_info AS mi ON mp.match_id = mi.match_id\\nWHERE\\n    mp.account_id = {johnpyp_account_id}\\n    AND (mi.average_badge_team0 > {ascendant_badge_id} OR mi.average_badge_team1 > {ascendant_badge_id});\\n\\"\\"\\"\\nresult = clickhouse_query(sql=query)\\nprint(f\\"Number of matches: {result}\\")\\n</code>"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-call", "content": [{"type": "text", "text": "Calling tools:\\n[{\'id\': \'call_3\', \'type\': \'function\', \'function\': {\'name\': \'python_interpreter\', \'arguments\': \'query = f\\"\\"\\"\\\\nSELECT\\\\n    COUNT(DISTINCT mp.match_id)\\\\nFROM\\\\n    match_player AS mp\\\\nJOIN\\\\n    match_info AS mi ON mp.match_id = mi.match_id\\\\nWHERE\\\\n    mp.account_id = {johnpyp_account_id}\\\\n    AND (mi.average_badge_team0 > {ascendant_badge_id} OR mi.average_badge_team1 > {ascendant_badge_id});\\\\n\\"\\"\\"\\\\nresult = clickhouse_query(sql=query)\\\\nprint(f\\"Number of matches: {result}\\")\'}}]"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-response", "content": [{"type": "text", "text": "Observation:\\nExecution logs:\\nNumber of matches: [{\'COUNTDistinct(match_id)\': \'95\'}]\\nLast output from code snippet:\\nNone"}], "tool_calls": null, "raw": null, "token_usage": null}], "plots": ["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"]}\n\n',
    'event: agentStep\ndata: {"type": "action_output", "data": "ActionOutput(output=\'95\', is_final_answer=True)"}\n\n',
    'event: agentStep\ndata: {"type": "action", "data": [{"role": "assistant", "content": [{"type": "text", "text": "Thought: I have successfully retrieved the count of matches. I will extract the value and provide the final answer.\\n<code>\\nmatch_count = result[0][\'COUNTDistinct(match_id)\']\\nfinal_answer(match_count)\\n</code>"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-call", "content": [{"type": "text", "text": "Calling tools:\\n[{\'id\': \'call_4\', \'type\': \'function\', \'function\': {\'name\': \'python_interpreter\', \'arguments\': \\"match_count = result[0][\'COUNTDistinct(match_id)\']\\\\nfinal_answer(match_count)\\"}}]"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-response", "content": [{"type": "text", "text": "Observation:\\nExecution logs:\\nLast output from code snippet:\\n95"}], "tool_calls": null, "raw": null, "token_usage": null}]}\n\n',
    'event: agentStep\ndata: {"type": "final_answer", "data": "95"}\n\n',
    "event: memoryId\ndata: d450bc53-9b2c-42af-bd29-5ba0ce57184c\n\n",
]


def get_model() -> ApiModel:
    if model := os.environ.get("MODEL"):
        if model.strip() in MODEL_CONFIGS:
            return MODEL_CONFIGS[model.strip()]()

    if "GEMINI_API_KEY" in os.environ:
        LOGGER.info("Using Google Gemini Flash Model")
        return MODEL_CONFIGS["gemini-flash"]()
    elif "HF_TOKEN" in os.environ:
        LOGGER.info("Using Hugging Face Inference API")
        return MODEL_CONFIGS["hf"]()
    else:
        raise ValueError(f"Invalid model: {model}")


def get_message_store() -> MessageStore:
    if "REDIS_HOST" in os.environ:
        LOGGER.info("Using Redis Message Store")
        return RedisMessageStore()
    else:
        LOGGER.info("Using In-Memory Message Store")
        return MemoryMessageStore()
