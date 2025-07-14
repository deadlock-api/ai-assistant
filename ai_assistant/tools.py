import requests
import sqlglot
from smolagents import tool


@tool
def search_steam_profile(name_or_id: str) -> int:
    """
    Retrieve the account id of a player by name or account id.

    Args:
        name_or_id: The name or account id of the player.
    """
    return requests.get(
        f"https://api.deadlock-api.com/v1/players/steam-search?search_query={name_or_id}"
    ).json()[0]["account_id"]


@tool
def hero_name_to_id(hero_name: str) -> int | str:
    """
    Retrieve the hero id by a given hero name.

    Args:
        hero_name: The name of the hero.
    """
    sql = f"""
    SELECT id
    FROM heroes
    WHERE editDistanceUTF8(lower('{hero_name}'), lower(name)) < 2
    ORDER BY editDistanceUTF8(lower('{hero_name}'), lower(name))
    LIMIT 1
    """
    try:
        return requests.get(
            "https://api.deadlock-api.com/v1/sql", params={"query": sql}
        ).json()[0]["id"]
    except KeyError:
        return "Hero not found"


@tool
def query(sql: str) -> list[dict]:
    """
    Query the Clickhouse DB containing data about deadlock using a SQL query.

    Args:
        sql: The query to perform. This should be correct Clickhouse SQL.
    """
    sql = sqlglot.transpile(sql, write="clickhouse")[0]
    results = requests.get(
        "https://api.deadlock-api.com/v1/sql", params={"query": sql}
    ).json()
    if len(results) == 0:
        raise Exception("No results found!")
    return results
