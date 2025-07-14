from ai_assistant.tools import (
    hero_name_to_id,
    search_steam_profile,
    item_name_to_id,
    rank_to_badge,
)


def test_hero_name_to_id():
    assert hero_name_to_id("Wraith") == 7
    assert hero_name_to_id("Wrath") == 7


def test_item_name_to_id():
    assert item_name_to_id("Extended Magazine") == 1548066885
    assert item_name_to_id("Extended Magazin") == 1548066885


def test_rank_to_badge():
    assert rank_to_badge("Ascendant", 4) == 104
    assert rank_to_badge("Phantom") == 90


def test_search_steam_profile():
    assert search_steam_profile("johnpyp") == 127331261
