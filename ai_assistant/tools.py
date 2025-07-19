import Levenshtein
import requests
import sqlglot
from smolagents import tool


@tool
def search_steam_profile(name_or_id: str) -> int:
    """
    Retrieve the account id of a player by name or account id.

    Args:
        name_or_id: The name or account id of the player.

    Returns:
        int: Account ID
    """
    try:
        return requests.get(f"https://api.deadlock-api.com/v1/players/steam-search?search_query={name_or_id}").json()[
            0
        ]["account_id"]
    except (KeyError, IndexError):
        raise ValueError(f"Player with name or ID '{name_or_id}' not found.")


@tool
def rank_to_badge(rank_name: str, rank_tier: int | None = 0) -> int | str:
    """
    Retrieve badge index for rank and subtier.

    Args:
        rank_name: The rank, for example: Ascendant
        rank_tier: Optional subrank, for example: 4

    Returns:
        int | str: Hero ID or "Item not found"
    """
    ranks = requests.get("https://assets.deadlock-api.com/v2/ranks").json()
    closest_rank = min(ranks, key=lambda rank: Levenshtein.distance(rank["name"].lower(), rank_name))
    return closest_rank["tier"] * 10 + rank_tier


@tool
def hero_name_to_id(hero_name: str) -> int | str:
    """
    Retrieve the hero id by a given hero name.

    Args:
        hero_name: The name of the hero.

    Returns:
        int | str: Hero ID or "Item not found"
    """
    sql = f"""
    SELECT id
    FROM heroes
    WHERE editDistanceUTF8(lower('{hero_name}'), lower(name)) < 2
    ORDER BY editDistanceUTF8(lower('{hero_name}'), lower(name))
    LIMIT 1
    """
    try:
        return requests.get("https://api.deadlock-api.com/v1/sql", params={"query": sql}).json()[0]["id"]
    except (KeyError, IndexError):
        return "Hero not found"


@tool
def item_name_to_id(item_name: str) -> int | str:
    """
    Retrieve the item id by a given item name.

    Args:
        item_name: The name of the item.

    Returns:
        int | str: Item ID or "Item not found"
    """
    sql = f"""
    SELECT id
    FROM items
    WHERE editDistanceUTF8(lower('{item_name}'), lower(name)) < 7
    ORDER BY editDistanceUTF8(lower('{item_name}'), lower(name))
    LIMIT 1
    """
    try:
        return requests.get("https://api.deadlock-api.com/v1/sql", params={"query": sql}).json()[0]["id"]
    except (KeyError, IndexError):
        return "Item not found"


@tool
def clickhouse_query(sql: str) -> list[dict]:
    """
    Query the Clickhouse DB containing data about deadlock using a SQL query. Results in a list of dict with entries column_name -> value
    You can at most query 100k rows at a time, so be careful with your queries and try to aggregate the data in SQL.

    Args:
        sql: The query to perform. This should be correct Clickhouse SQL.

    Returns:
        list[dict[str, Any]]: Query Result
    """
    sql = sqlglot.transpile(sql, write="clickhouse")[0]
    results = requests.get("https://api.deadlock-api.com/v1/sql", params={"query": sql}).json()
    if len(results) == 0:
        raise Exception("No results found!")
    return results


ALL_TOOLS = [
    hero_name_to_id,
    item_name_to_id,
    rank_to_badge,
    search_steam_profile,
    clickhouse_query,
]
