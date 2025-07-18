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
    "mid_boss",
    "objectives",
    "personastate",
    "is_high_skill_range_parties",
    "low_pri_pool",
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


if __name__ == "__main__":
    tables = list_clickhouse_tables()
    for table in tables:
        print(format_table_schema(table))
