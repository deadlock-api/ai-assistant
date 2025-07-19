import logging
import os

from smolagents import LiteLLMModel, InferenceClientModel, ApiModel

from ai_assistant.message_store import MessageStore, RedisMessageStore, MemoryMessageStore
from ai_assistant.utils import format_table_schema, list_clickhouse_tables

LOGGER = logging.getLogger(__name__)

DO_RELEVANCY_CHECK = os.environ.get("DO_RELEVANCY_CHECK", "false").lower() in ("true", "1", "yes")

TABLES_CONTEXT = "\n\n".join(format_table_schema(table) for table in list_clickhouse_tables())
AGENT_INSTRUCTIONS = f"Available Clickhouse Tables:\n{TABLES_CONTEXT}"

MODEL_CONFIGS = {
    "gemini-flash-lite": lambda: LiteLLMModel(model_id="gemini-2.5-flash-lite-preview-06-17"),
    "gemini-flash": lambda: LiteLLMModel(model_id="gemini/gemini-2.5-flash"),
    "gemini-pro": lambda: LiteLLMModel(model_id="gemini/gemini-2.5-pro"),
    "ollama": lambda: LiteLLMModel(model_id="ollama/qwen2.5-coder:14b"),
    "hf": lambda: InferenceClientModel(),
}
DEFAULT_LIGHT_MODEL = "gemini-2.5-flash-lite-preview-06-17"

REPLAY = [
    'event: agentStep\ndata: {"type": "action_output", "data": "ActionOutput(output=None, is_final_answer=False)"}\n\n',
    'event: agentStep\ndata: {"type": "action", "data": [{"role": "assistant", "content": [{"type": "text", "text": "<code>\\njohnpyp_account_id = search_steam_profile(name_or_id=\\"johnpyp\\")\\nprint(f\\"Johnpyp\'s account ID: {johnpyp_account_id}\\")\\n</code>"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-call", "content": [{"type": "text", "text": "Calling tools:\\n[{\'id\': \'call_1\', \'type\': \'function\', \'function\': {\'name\': \'python_interpreter\', \'arguments\': \'johnpyp_account_id = search_steam_profile(name_or_id=\\"johnpyp\\")\\\\nprint(f\\"Johnpyp\\\\\'s account ID: {johnpyp_account_id}\\")\'}}]"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-response", "content": [{"type": "text", "text": "Observation:\\nExecution logs:\\nJohnpyp\'s account ID: 127331261\\nLast output from code snippet:\\nNone"}], "tool_calls": null, "raw": null, "token_usage": null}]}\n\n',
    'event: agentStep\ndata: {"type": "action_output", "data": "ActionOutput(output=None, is_final_answer=False)"}\n\n',
    'event: agentStep\ndata: {"type": "action", "data": [{"role": "assistant", "content": [{"type": "text", "text": "Thought: I have Johnpyp\'s account ID. Next, I need to find the badge ID for \'Ascendant\' so I can filter matches where the average badge was greater than it. I will use the `rank_to_badge` tool for this.\\n<code>\\nascendant_badge_id = rank_to_badge(rank_name=\\"Ascendant\\")\\nprint(f\\"Ascendant badge ID: {ascendant_badge_id}\\")\\n</code>"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-call", "content": [{"type": "text", "text": "Calling tools:\\n[{\'id\': \'call_2\', \'type\': \'function\', \'function\': {\'name\': \'python_interpreter\', \'arguments\': \'ascendant_badge_id = rank_to_badge(rank_name=\\"Ascendant\\")\\\\nprint(f\\"Ascendant badge ID: {ascendant_badge_id}\\")\'}}]"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-response", "content": [{"type": "text", "text": "Observation:\\nExecution logs:\\nAscendant badge ID: 100\\nLast output from code snippet:\\nNone"}], "tool_calls": null, "raw": null, "token_usage": null}]}\n\n',
    'event: agentStep\ndata: {"type": "action_output", "data": "ActionOutput(output=None, is_final_answer=False)"}\n\n',
    'event: agentStep\ndata: {"type": "action", "data": [{"role": "assistant", "content": [{"type": "text", "text": "Thought: I have both Johnpyp\'s account ID and the Ascendant badge ID. Now I need to query the `match_player` and `match_info` tables to find matches where Johnpyp participated and the `average_badge_team0` or `average_badge_team1` was greater than the Ascendant badge ID. I will count the number of such unique matches.\\nI need to join `match_player` with `match_info` on `match_id`. Then filter by `account_id` and check the average badge of both teams.\\n\\nGiven that `average_badge_team0` and `average_badge_team1` are nullable, I should handle `NULL` values by considering them as 0 or simply ignore them in the comparison if it\'s more appropriate, but usually, `NULL` values are excluded from comparisons. In this case, comparing `NULL` with `> 100` would evaluate to `NULL`, so records with `NULL` average badges won\'t be counted.\\n\\nI will use a SQL query to count the distinct matches where Johnpyp played and either team\'s average badge was greater than 100.\\n<code>\\nquery = f\\"\\"\\"\\nSELECT\\n    COUNT(DISTINCT mp.match_id)\\nFROM\\n    match_player AS mp\\nJOIN\\n    match_info AS mi ON mp.match_id = mi.match_id\\nWHERE\\n    mp.account_id = {johnpyp_account_id}\\n    AND (mi.average_badge_team0 > {ascendant_badge_id} OR mi.average_badge_team1 > {ascendant_badge_id});\\n\\"\\"\\"\\nresult = clickhouse_query(sql=query)\\nprint(f\\"Number of matches: {result}\\")\\n</code>"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-call", "content": [{"type": "text", "text": "Calling tools:\\n[{\'id\': \'call_3\', \'type\': \'function\', \'function\': {\'name\': \'python_interpreter\', \'arguments\': \'query = f\\"\\"\\"\\\\nSELECT\\\\n    COUNT(DISTINCT mp.match_id)\\\\nFROM\\\\n    match_player AS mp\\\\nJOIN\\\\n    match_info AS mi ON mp.match_id = mi.match_id\\\\nWHERE\\\\n    mp.account_id = {johnpyp_account_id}\\\\n    AND (mi.average_badge_team0 > {ascendant_badge_id} OR mi.average_badge_team1 > {ascendant_badge_id});\\\\n\\"\\"\\"\\\\nresult = clickhouse_query(sql=query)\\\\nprint(f\\"Number of matches: {result}\\")\'}}]"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-response", "content": [{"type": "text", "text": "Observation:\\nExecution logs:\\nNumber of matches: [{\'COUNTDistinct(match_id)\': \'95\'}]\\nLast output from code snippet:\\nNone"}], "tool_calls": null, "raw": null, "token_usage": null}]}\n\n',
    'event: agentStep\ndata: {"type": "action_output", "data": "ActionOutput(output=\'95\', is_final_answer=True)"}\n\n',
    'event: agentStep\ndata: {"type": "action", "data": [{"role": "assistant", "content": [{"type": "text", "text": "Thought: I have successfully retrieved the count of matches. I will extract the value and provide the final answer.\\n<code>\\nmatch_count = result[0][\'COUNTDistinct(match_id)\']\\nfinal_answer(match_count)\\n</code>"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-call", "content": [{"type": "text", "text": "Calling tools:\\n[{\'id\': \'call_4\', \'type\': \'function\', \'function\': {\'name\': \'python_interpreter\', \'arguments\': \\"match_count = result[0][\'COUNTDistinct(match_id)\']\\\\nfinal_answer(match_count)\\"}}]"}], "tool_calls": null, "raw": null, "token_usage": null}, {"role": "tool-response", "content": [{"type": "text", "text": "Observation:\\nExecution logs:\\nLast output from code snippet:\\n95"}], "tool_calls": null, "raw": null, "token_usage": null}]}\n\n',
    'event: agentStep\ndata: {"type": "final_answer", "data": "95"}\n\n',
    "event: memoryId\ndata: d450bc53-9b2c-42af-bd29-5ba0ce57184c\n\n",
]


def get_model() -> ApiModel:
    if model := os.environ.get("MODEL"):
        if model in MODEL_CONFIGS:
            return MODEL_CONFIGS[model]()

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
