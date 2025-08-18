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
3. **Uses Discord markdown formatting** for rich presentation
4. **Includes short explanatory context** - Always explain:
   - What the statistics/data actually mean
   - Why the information is relevant
   - Any important context the user should understand
   - Any assumptions or simplifications you made
   - Don't go into too much detail on this, just 1-2 sentences max

5. **Maintains Deadlock game focus** - Ensure all responses are relevant to Deadlock gameplay, statistics, heroes, items, etc.

The response should feel like a knowledgeable gaming expert is providing a thoughtful, complete answer based on the full conversation context, not just a quick reply to the latest question.

Format your response to be engaging, informative, and visually appealing when displayed in Discord.
Keep the response as short as possible while still being comprehensive."""


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

    def format_conversation(self, memory: AgentMemory, markdown: bool = True) -> str:
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
                "Based on this complete conversation history, generate a comprehensive, "
                f"{'Discord Markdown' if markdown else 'plain text'} formatted response that synthesizes all the information "
                "and provide a polished answer with explanatory context. "
                "Keep it short, simple, and to the point!"
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
