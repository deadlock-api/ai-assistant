import logging
import os
import re
from typing import List

from google import genai
from google.genai.types import GenerateContentConfig

from ai_assistant.configs import DEFAULT_LIGHT_MODEL
from ai_assistant.utils import list_heroes, list_items

LOGGER = logging.getLogger(__name__)

RELEVANCY_SYSTEM_PROMPT = """You are a relevancy checker for a Deadlock game assistant.

Deadlock is a 6v6 team-based third-person shooter and MOBA where:
- 12 players control unique "heroes" across three lanes
- Players gain "souls" and "ability points" by killing troopers, denizens, and other players
- Players buy items in "curiosity shops" and upgrade abilities
- The goal is to destroy the opposing team's "patron"
- The game has over 30 playable heroes with unique weapons, stats, and abilities
- Takes place in an alternative version of New York City with occult references
- Features ziplines, teleporters, and various game mechanisms

Players commonly use abbreviations for hero and item names. For example:
- "GT" for "Grey Talon"
- "QSR" for "Quicksilver Reload"
- "LG" for "Lady Geist"
- Accept any reasonable abbreviation based on first letters of significant words

IMPORTANT: Since this is a Deadlock-specific assistant, broad gaming questions should be assumed to be about Deadlock even if not explicitly mentioned. Examples of implicitly Deadlock-related questions:
- "Who is the best player?" (means: best Deadlock player)
- "Am I a good player?" (means: am I good at Deadlock)
- "What's the meta?" (means: Deadlock meta)
- "Best builds?" (means: best Deadlock builds)
- "How do I improve?" (means: improve at Deadlock)
- "What should I focus on?" (means: in Deadlock gameplay)

Respond with ONLY "YES" if the user prompt is asking about:
- Deadlock gameplay, mechanics, strategies, or rules
- Deadlock heroes, abilities, items, or builds (including abbreviations)
- Deadlock statistics, match data, or player performance
- Deadlock rankings, badges, or progression
- General gaming questions that would apply to Deadlock (best players, improvement tips, meta, builds, etc.)
- Any other aspect specifically related to the Deadlock game

Respond with ONLY "NO" if the prompt is about:
- Other games explicitly mentioned (CS:GO, Dota, League of Legends, etc.)
- General life questions, coding help, or unrelated topics
- Requests that clearly don't involve gaming or Deadlock content

Be liberal with gaming-related questions since this is a Deadlock assistant."""


class RelevancyChecker:
    def __init__(self):
        self.model_id = os.environ.get("LIGHT_MODEL", DEFAULT_LIGHT_MODEL)
        self.client = genai.Client()
        self.heroes: list[str] = []
        self.items: list[str] = []
        self._load_game_data()

    def _load_game_data(self):
        try:
            self.heroes = list_heroes()
            self.items = list_items()
            LOGGER.info(f"Loaded {len(self.heroes)} heroes and {len(self.items)} items for relevancy checking")
        except Exception as e:
            LOGGER.error(f"Failed to load game data for relevancy checking: {e}")
            self.heroes = []
            self.items = []

    def _generate_hero_abbreviations(self, hero_name: str) -> List[str]:
        abbreviations = []
        words = hero_name.replace("&", "").split()
        if len(words) > 1:
            abbreviations.append("".join(word[0].upper() for word in words))
        return abbreviations

    def _generate_item_abbreviations(self, item_name: str) -> List[str]:
        abbreviations = []
        common_words = {"the", "of", "and", "a", "an", "in", "on", "at", "to", "for", "with", "&"}
        words = [word for word in item_name.replace("&", "").split() if word.lower() not in common_words]

        if len(words) > 1:
            abbreviations.append("".join(word[0].upper() for word in words))
        if len(words) >= 2:
            abbreviations.append("".join(word[0].upper() for word in words[:2]))
        return abbreviations

    def _check_keyword_match(self, prompt: str) -> bool:
        prompt_lower = prompt.lower()

        for hero in self.heroes:
            if re.search(r"\b" + re.escape(hero.lower()) + r"\b", prompt_lower):
                LOGGER.info(f"Found hero match: '{hero}' in prompt")
                return True

            for abbrev in self._generate_hero_abbreviations(hero):
                if len(abbrev) >= 3 and re.search(r"\b" + re.escape(abbrev.lower()) + r"\b", prompt_lower):
                    LOGGER.info(f"Found hero abbreviation match: '{abbrev}' ({hero}) in prompt")
                    return True

        for item in self.items:
            if re.search(r"\b" + re.escape(item.lower()) + r"\b", prompt_lower):
                LOGGER.info(f"Found item match: '{item}' in prompt")
                return True

            for abbrev in self._generate_item_abbreviations(item):
                if len(abbrev) >= 3 and re.search(r"\b" + re.escape(abbrev.lower()) + r"\b", prompt_lower):
                    LOGGER.info(f"Found item abbreviation match: '{abbrev}' ({item}) in prompt")
                    return True

        return False

    def is_relevant(self, prompt: str) -> bool:
        if self._check_keyword_match(prompt):
            return True

        try:
            if not os.environ.get("GEMINI_API_KEY"):
                LOGGER.warning("GEMINI_API_KEY not set, will not run relevancy check")
                return True

            heroes_with_abbrevs = []
            for hero in self.heroes:
                abbrevs = self._generate_hero_abbreviations(hero)
                heroes_with_abbrevs.append(f"{hero} ({', '.join(abbrevs)})")

            items_with_abbrevs = []
            for item in self.items:
                abbrevs = self._generate_item_abbreviations(item)
                items_with_abbrevs.append(f"{item} ({', '.join(abbrevs)})")

            heroes_context = f"Known Heroes: {', '.join(heroes_with_abbrevs)}" if self.heroes else ""
            items_context = f"Known Items: {', '.join(items_with_abbrevs)}" if self.items else ""

            full_prompt = (
                f"{RELEVANCY_SYSTEM_PROMPT}\n\n"
                f"{heroes_context}\n"
                f"{items_context}\n\n"
                f"User prompt: {prompt}\n\n"
                f"Respond with exactly one word: YES or NO"
            )

            response = self.client.models.generate_content(
                model=self.model_id,
                contents=full_prompt,
                config=GenerateContentConfig(
                    max_output_tokens=1,
                    temperature=0.0,
                    response_mime_type="text/x.enum",
                    response_schema={
                        "type": "string",
                        "enum": ["YES", "NO"],
                    },
                ),
            )

            result = response.text.strip().upper()
            LOGGER.info(f"Light model ({self.model_id}) relevancy check: '{prompt[:100]}...' -> {result}")
            return result == "YES"
        except Exception as e:
            LOGGER.error(f"Error during light model relevancy check: {e}")
            return True


if __name__ == "__main__":
    checker = RelevancyChecker()
    test_prompts = [
        "Tell me about the Deadlock game mechanics.",
        "What are the best heroes in Deadlock?",
        "How do I play Dota 2?",
        "Can you help me with Python coding?",
        "What is the best item build for a Deadlock hero?",
        "How does Abrams work?",
        "What does Quicksilver Reload do?",
        "Tell me about Infernus abilities",
    ]

    for prompt in test_prompts:
        print(f"Prompt: {prompt} | Relevant: {checker.is_relevant(prompt)}")
