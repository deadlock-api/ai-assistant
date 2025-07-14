from ai_assistant.tools import hero_name_to_id, search_steam_profile


def test_hero_name_to_id():
    assert hero_name_to_id("Wraith") == 7


def test_search_steam_profile():
    assert search_steam_profile("johnpyp") == 127331261
