import logging
import os

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

Respond with ONLY "YES" if the user prompt is asking about:
- Deadlock gameplay, mechanics, strategies, or rules
- Deadlock heroes, abilities, items, or builds
- Deadlock statistics, match data, or player performance
- Deadlock rankings, badges, or progression
- Any other aspect specifically related to the Deadlock game

Respond with ONLY "NO" if the prompt is about:
- Other games (CS:GO, Dota, League of Legends, etc.)
- General life questions, coding help, or unrelated topics
- Requests that don't involve Deadlock game content

Be strict - only accept prompts that are clearly about Deadlock."""


class RelevancyChecker:
    def __init__(self):
        self.model_id = os.environ.get("LIGHT_MODEL", "gemini-2.5-flash-lite-preview-06-17")

    def is_relevant(self, prompt: str) -> bool:
        try:
            import google.generativeai as genai

            if not os.environ.get("GEMINI_API_KEY"):
                LOGGER.warning("GEMINI_API_KEY not set, using keyword filter")
                return self._keyword_filter(prompt)

            genai.configure(api_key=os.environ["GEMINI_API_KEY"])
            model = genai.GenerativeModel(self.model_id)

            full_prompt = (
                f"{RELEVANCY_SYSTEM_PROMPT}\n\nUser prompt: {prompt}\n\nRespond with exactly one word: YES or NO"
            )

            response = model.generate_content(
                full_prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=1,
                    temperature=0.0,
                ),
            )

            result = response.text.strip().upper()
            LOGGER.info(f"Light model ({self.model_id}) relevancy check: '{prompt[:100]}...' -> {result}")
            return result == "YES"

        except ImportError as e:
            LOGGER.warning(f"Google GenAI import failed: {e}, using keyword filter")
            return self._keyword_filter(prompt)
        except Exception as e:
            LOGGER.error(f"Error during light model relevancy check: {e}, falling back to keyword filter")
            return self._keyword_filter(prompt)

    def _keyword_filter(self, prompt: str) -> bool:
        banned_keywords = [
            "league of legends",
            "dota 2",
            "dota",
            "lol",
            "valorant",
            "cs:go",
            "counter-strike",
            "overwatch",
            "apex legends",
            "fortnite",
            "minecraft",
            "world of warcraft",
            "wow",
            "code",
            "coding",
            "programming",
            "python",
            "javascript",
            "html",
            "css",
            "help me",
            "how to",
            "tutorial",
            "learn",
            "teach me",
            "explain",
            "what is",
            "weather",
            "news",
            "politics",
            "recipe",
            "cooking",
            "health",
            "medical",
            "finance",
        ]

        prompt_lower = prompt.lower()
        for keyword in banned_keywords:
            if keyword in prompt_lower:
                LOGGER.info(f"Keyword filter rejected: '{keyword}' found in prompt")
                return False

        deadlock_keywords = [
            "deadlock",
            "hero",
            "souls",
            "patron",
            "trooper",
            "denizen",
            "zipline",
            "curiosity shop",
            "badge",
            "rank",
            "ascendant",
            "phantom",
            "eternus",
            "mirage",
            "infernus",
            "lady geist",
            "lash",
            "mcginnis",
            "paradox",
            "pocket",
            "seven",
            "shiv",
            "vindicta",
            "warden",
            "wraith",
            "yamato",
        ]

        for keyword in deadlock_keywords:
            if keyword in prompt_lower:
                LOGGER.info(f"Keyword filter accepted: '{keyword}' found in prompt")
                return True

        LOGGER.info("No clear Deadlock keywords found, rejecting")
        return False
