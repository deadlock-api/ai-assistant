import logging
import os

from google import genai
from google.genai.types import GenerateContentConfig

from ai_assistant.configs import DEFAULT_LIGHT_MODEL

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

Be strict - only accept prompts that could potentially be about Deadlock."""


class RelevancyChecker:
    def __init__(self):
        self.model_id = os.environ.get("LIGHT_MODEL", DEFAULT_LIGHT_MODEL)
        self.client = genai.Client()

    def is_relevant(self, prompt: str) -> bool:
        try:
            if not os.environ.get("GEMINI_API_KEY"):
                LOGGER.warning("GEMINI_API_KEY not set, will not run relevancy check")
                return True

            full_prompt = (
                f"{RELEVANCY_SYSTEM_PROMPT}\n\nUser prompt: {prompt}\n\nRespond with exactly one word: YES or NO"
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
    ]

    for prompt in test_prompts:
        print(f"Prompt: {prompt} | Relevant: {checker.is_relevant(prompt)}")
