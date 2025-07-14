import requests


def list_clickhouse_tables() -> list[str]:
    return [
        t
        for t in requests.get("https://api.deadlock-api.com/v1/sql/tables").json()
        if t
        not in [
            "active_matches",
            "player_match_history",
            "glicko",
            "player_card",
            "mmr_history",
            "hero_mmr_history",
            "match_salts",
        ]
    ]


def schema(table: str) -> dict[str, str]:
    return {
        column["name"]: column["type"]
        for column in requests.get(f"https://api.deadlock-api.com/v1/sql/tables/{table}/schema").json()
    }
