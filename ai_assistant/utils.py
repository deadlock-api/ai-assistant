import itertools
import os

import requests

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


if __name__ == "__main__":
    tables = list_clickhouse_tables()
    for table in tables:
        print(format_table_schema(table))
