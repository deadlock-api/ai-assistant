import base64
import itertools
import logging
import os

import redis
import requests
from smolagents import AgentMemory, ChatMessage

LOGGER = logging.getLogger(__name__)

HOST = os.environ.get("REDIS_HOST", "localhost")
PORT = os.environ.get("REDIS_PORT", 6379)
PASS = os.environ.get("REDIS_PASSWORD")

EXCLUDED_TABLES = {
    "active_matches",
    "player_match_history",
    "glicko",
    "player_card",
    "mmr_history",
    "hero_mmr_history",
    "match_salts",
}
EXCLUDED_COLUMN_PREFIXES = {
    "match_mode",
    "game_mode",
    "match_outcome",
    "death_details",
    "max_",
    "book_reward",
    "objectives",
    "personastate",
    "new_player_pool",
    "is_high_skill_range_parties",
    "low_pri_pool",
    "game_mode_version",
    "profileurl",
    "avatar",
}


def list_clickhouse_tables() -> list[str]:
    return [t for t in requests.get("https://api.deadlock-api.com/v1/sql/tables").json() if t not in EXCLUDED_TABLES]


def schema(table: str) -> dict[str, str]:
    return {
        column["name"]: column["type"]
        for column in requests.get(f"https://api.deadlock-api.com/v1/sql/tables/{table}/schema").json()
    }


def format_table_schema(table: str) -> str:
    columns = [
        f"{name}: {type_}"
        for name, type_ in schema(table).items()
        if not any(name.startswith(prefix) for prefix in EXCLUDED_COLUMN_PREFIXES)
    ]
    return f"## Table: {table}\n" + "\n".join(columns)


def list_heroes() -> list[str]:
    response = requests.get("https://assets.deadlock-api.com/v2/heroes")
    heroes_data = response.json()
    return [hero["name"] for hero in heroes_data]


def list_items() -> list[str]:
    response = requests.get("https://assets.deadlock-api.com/v2/items/by-type/upgrade")
    items_data = response.json()
    return [item["name"] for item in items_data if item.get("shopable", False)]


gemini_api_keys = os.environ.get("GEMINI_API_KEYS", os.environ.get("GEMINI_API_KEY", "")).split(",")
gemini_api_keys_iter = itertools.cycle(gemini_api_keys) if gemini_api_keys else None


def get_gemini_api_key() -> str:
    if gemini_api_keys_iter:
        return next(gemini_api_keys_iter).strip()
    raise ValueError("No valid Gemini API keys found in environment variables.")


def extract_messages_from_memory(memory: AgentMemory) -> list[ChatMessage]:
    messages = []
    if memory.system_prompt:
        messages.extend(memory.system_prompt.to_messages())
    messages.extend(m for step in memory.steps for m in step.to_messages())
    return messages


def format_messages_for_prompt(messages: list[ChatMessage]) -> str:
    formatted_parts = []
    for msg in messages:
        if msg.role == "user":
            formatted_parts.append(f"USER: {msg.content}")
        else:
            formatted_parts.append(f"ASSISTANT: {msg.content}")
    return "\n\n".join(formatted_parts)


def load_image_as_base64(file: str) -> str | None:
    if os.path.exists(file):
        with open(file, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    return None


def is_valid_captcha_token(captcha_token: str, remoteip: str | None = None) -> bool:
    conn = redis.Redis(host=HOST, port=PORT, password=PASS)
    if conn.get(captcha_token) is not None:
        return True
    is_valid = validate_captcha(captcha_token, remoteip)
    if is_valid:
        conn.set(captcha_token, 1, ex=7 * 24 * 60 * 60)  # 7 days
    return is_valid


def validate_captcha(captcha_token: str, remoteip: str | None = None) -> bool:
    secret = os.environ.get("CAPTCHA_SECRET")
    if not secret:
        return True
    url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    try:
        data = {"secret": secret, "response": captcha_token}
        if remoteip:
            data["remoteip"] = remoteip
        response = requests.post(url, data=data)
        response.raise_for_status()
        return response.json()["success"]
    except Exception:
        LOGGER.warning("Captcha validation failed")
        return False


if __name__ == "__main__":
    tables = list_clickhouse_tables()
    for table in tables:
        print(format_table_schema(table))
