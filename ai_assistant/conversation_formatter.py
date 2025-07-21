import logging
import os

from google import genai
from google.genai.types import GenerateContentConfig
from pydantic import BaseModel, Field
from smolagents import AgentMemory

from ai_assistant import utils
from ai_assistant.configs import DEFAULT_LIGHT_MODEL

LOGGER = logging.getLogger(__name__)

CONVERSATION_FORMATTER_SYSTEM_PROMPT = """You are a conversation formatter for a Deadlock game assistant.

Your task is to analyze the complete conversation history between a user and an AI assistant, then generate a comprehensive, well-formatted response that:

1. **Synthesizes the entire conversation context** - Don't just respond to the latest message, but consider the full conversation flow
2. **Provides a polished, complete answer** - Give a beautiful, comprehensive response that addresses the user's needs
3. **Uses Discord markdown formatting** for rich presentation:
   - **Bold text** for emphasis and headers
   - *Italic text* for subtle emphasis
   - `code blocks` for technical terms, numbers, and data
   - ```code blocks``` for longer code or data
   - • Bullet points for lists
   - 1. Numbered lists for sequences
   - > Quotes for important information

4. **Includes explanatory context** - Always explain:
   - How you arrived at your conclusions
   - What the statistics/data actually mean
   - Why the information is relevant
   - Any important context the user should understand

5. **Maintains Deadlock game focus** - Ensure all responses are relevant to Deadlock gameplay, statistics, heroes, items, etc.

The response should feel like a knowledgeable gaming expert is providing a thoughtful, complete answer based on the full conversation context, not just a quick reply to the latest question.

Format your response to be engaging, informative, and visually appealing when displayed in Discord."""


class ConversationFormatterResult(BaseModel):
    formatted_response: str = Field(
        ...,
        description="A comprehensive, Discord markdown formatted response based on the full conversation history",
    )


class ConversationFormatter:
    def __init__(
        self,
        model_id=os.environ.get("LIGHT_MODEL", DEFAULT_LIGHT_MODEL),
    ):
        self.model_id = model_id
        self.client = genai.Client()

    def format_conversation(self, memory: AgentMemory) -> str:
        try:
            # Extract and format conversation history
            conversation_history = utils.extract_messages_from_memory(memory)
            formatted_conversation = utils.format_messages_for_prompt(conversation_history)

            if not formatted_conversation.strip():
                LOGGER.warning("No conversation history found to format")
                return "No conversation history available to format."

            # Create the full prompt for the LLM
            full_prompt = (
                f"{CONVERSATION_FORMATTER_SYSTEM_PROMPT}\n\n"
                f"CONVERSATION HISTORY:\n{formatted_conversation}\n\n"
                f"Based on this complete conversation history, generate a comprehensive, "
                f"Discord markdown formatted response that synthesizes all the information "
                f"and provides a polished answer with explanatory context."
            )

            # Call the light model
            self.client._api_client.api_key = utils.get_gemini_api_key()
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=full_prompt,
                config=GenerateContentConfig(temperature=0.3),
            )

            LOGGER.info(f"Light model ({self.model_id}) conversation formatting completed successfully")
            return response.text
        except Exception as e:
            LOGGER.error(f"Error during conversation formatting: {e}")
            return f"Error formatting conversation: {str(e)}"
