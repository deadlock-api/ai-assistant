import logging
import os
import re
import urllib.parse

import Levenshtein
import pywikibot
import requests
import sqlglot
from pydantic import BaseModel
from smolagents import tool, AgentMemory, ToolCall, ActionStep
from functools import lru_cache

LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1000)
def get_items():
    return requests.get("https://assets.deadlock-api.com/v2/items/").json()


@lru_cache(maxsize=1000)
def get_heroes():
    return requests.get("https://assets.deadlock-api.com/v2/heroes").json()


@lru_cache(maxsize=1000)
def get_ranks():
    return requests.get("https://assets.deadlock-api.com/v2/ranks").json()


@tool
def search_steam_profile(name_or_id: str) -> int:
    """
    Retrieve the account id of a player by name or account id.

    Args:
        name_or_id: The name or account id of the player.

    Returns:
        int: Account ID
    """
    LOGGER.info(f"search_steam_profile({name_or_id})")
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
        rank_tier: Optional subrank (every rank has 6 subranks 1-6), for example: 4

    Returns:
        int | str: Hero ID or "Item not found"
    """
    LOGGER.info(f"rank_to_badge({rank_name}, {rank_tier})")
    ranks = get_ranks()
    closest_rank = min(ranks, key=lambda rank: Levenshtein.distance(rank["name"].lower(), rank_name))
    return closest_rank["tier"] * 10 + rank_tier


@tool
def badge_to_rank(badge: int) -> str:
    """
    Retrieve rank name and subtier for badge index.

    Args:
        badge: The badge index must be between 0 and 116.
            last digit represents the subrank, first digits represent the rank.

    Returns:
        str: Rank name and subtier, for example: Ascendant 4
    """
    LOGGER.info(f"badge_to_rank({badge})")
    rank_tier = badge % 10
    rank_name = get_ranks()[badge // 10]["name"]
    return f"{rank_name} {rank_tier}"


@tool
def hero_name_to_id(hero_name: str) -> int | str:
    """
    Retrieve the hero id by a given hero name.

    Args:
        hero_name: The name of the hero.

    Returns:
        int | str: Hero ID or "Item not found"
    """
    LOGGER.info(f"hero_name_to_id({hero_name})")
    sql = f"""
    SELECT id
    FROM heroes
    WHERE editDistanceUTF8(lower('{hero_name}'), lower(name)) < 2
    ORDER BY editDistanceUTF8(lower('{hero_name}'), lower(name))
    LIMIT 1
    """
    try:
        return requests.get(
            "https://api.deadlock-api.com/v1/sql",
            params={"query": sql},
            headers={"X-API-Key": os.environ.get("DEADLOCK_API_KEY", "")},
        ).json()[0]["id"]
    except (KeyError, IndexError):
        return "Hero not found"


@tool
def hero_id_to_name(hero_id: int) -> str:
    """
    Retrieve the hero name by a given hero id.

    Args:
        hero_id: The id of the hero.

    Returns:
        str: Hero name or "Hero not found"
    """
    LOGGER.info(f"hero_id_to_name({hero_id})")
    result = next((hero for hero in get_heroes() if hero["id"] == hero_id), None)
    if result:
        return result["name"]
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
    LOGGER.info(f"item_name_to_id({item_name})")
    sql = f"""
    SELECT id
    FROM items
    WHERE editDistanceUTF8(lower('{item_name}'), lower(name)) < 7
    ORDER BY editDistanceUTF8(lower('{item_name}'), lower(name))
    LIMIT 1
    """
    try:
        return requests.get(
            "https://api.deadlock-api.com/v1/sql",
            params={"query": sql},
            headers={"X-API-Key": os.environ.get("DEADLOCK_API_KEY", "")},
        ).json()[0]["id"]
    except (KeyError, IndexError):
        return "Item not found"


@tool
def item_id_to_name(item_id: int) -> str:
    """
    Retrieve the item name by a given item id.

    Args:
        item_id: The id of the item.

    Returns:
        str: Item name or "Item not found"
    """
    LOGGER.info(f"item_id_to_name({item_id})")
    item = next((item for item in get_items() if item["id"] == item_id or item["class_name"] == item_id), None)
    if item:
        return item["name"]
    return "Item not found"


@tool
def clickhouse_query(sql: str) -> list[dict]:
    """
    Query the Clickhouse DB containing data about deadlock using a SQL query. Results in a list of dict with entries column_name -> value
    You can at most query 100k rows at a time, so be careful with your queries and try to aggregate the data in SQL.
    Whenever you use the `match_info` table you MUST include `match_mode IN ('Ranked', 'Unranked')` in your where clause.

    Args:
        sql: The query to perform. This should be correct Clickhouse SQL.

    Returns:
        list[dict[str, Any]]: Query Result
    """
    LOGGER.info(f"clickhouse_query({sql})")
    if os.environ.get("USE_GLOT", "true") == "true":
        sql = sqlglot.transpile(sql, write="clickhouse")[0]
    results = requests.get(
        "https://api.deadlock-api.com/v1/sql",
        params={"query": sql},
        headers={"X-API-Key": os.environ.get("DEADLOCK_API_KEY", "")},
    ).json()
    if len(results) == 0:
        raise Exception("No results found!")
    return results


deadlockwiki_site = pywikibot.Site(url="https://deadlock.wiki/api.php")
page = pywikibot.Page(deadlockwiki_site, "Bebop")


@tool
def search_wiki_pages(query: str, limit: int = 10) -> list[str]:
    """
    Searches for pages on the Deadlock Wiki.

    Args:
        query (str): The search term.
        limit (int): The maximum number of results to return.

    Returns:
        List[str]: A list of page titles that match the query.
    """
    try:
        search_results = deadlockwiki_site.search(query, total=limit)
        return [page.title() for page in search_results]
    except Exception as e:
        return [f"An error occurred during search: {e}"]


@tool
def read_wiki_page(title: str) -> str:
    """
    Reads the content of a page from the Deadlock Wiki.

    Args:
        title (str): The title of the page to read.

    Returns:
        str: The content of the page, or an error message if the page does not exist.
    """
    try:
        page = pywikibot.Page(deadlockwiki_site, title)
        if page.exists():
            return page.text
        else:
            return f"Page with title '{title}' does not exist."
    except Exception as e:
        return f"An error occurred while reading the page: {e}"


def used_tools(memory: AgentMemory) -> list[ToolCall]:
    return [t for s in memory.steps if isinstance(s, ActionStep) for t in (s.tool_calls or [])]


class WikiReference(BaseModel):
    title: str
    url: str

    @classmethod
    def from_title(cls, title: str) -> "WikiReference":
        return cls(title=title, url=f"https://deadlock.wiki/{urllib.parse.quote_plus(title.replace(' ', '_'))}")

    def __hash__(self):
        return hash(self.title)


def get_wiki_references(memory: AgentMemory) -> list[WikiReference]:
    pattern = r"read_wiki_page\((?:title=)?\"([^\"]+)\"\)"
    return list(
        {
            WikiReference.from_title(match)
            for tool_call in used_tools(memory)
            for match in re.findall(pattern, tool_call.arguments)
        }
    )


ALL_TOOLS = [
    hero_name_to_id,
    hero_id_to_name,
    item_name_to_id,
    item_id_to_name,
    rank_to_badge,
    badge_to_rank,
    search_steam_profile,
    clickhouse_query,
    search_wiki_pages,
    read_wiki_page,
]
