import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, Generator
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from scalar_fastapi import get_scalar_api_reference
from smolagents import (
    CodeAgent,
    ActionStep,
    PlanningStep,
    ChatMessageStreamDelta,
    FinalAnswerStep,
    ActionOutput,
    ApiModel,
    AgentMemory,
)
from starlette.responses import RedirectResponse
from starlette.status import HTTP_308_PERMANENT_REDIRECT
from starlette.middleware.cors import CORSMiddleware

from ai_assistant import utils
from ai_assistant.configs import (
    MODEL_CONFIGS,
    AGENT_INSTRUCTIONS,
    get_model,
    get_message_store,
    REPLAY,
    DO_RELEVANCY_CHECK,
)
from ai_assistant.tools import ALL_TOOLS
from ai_assistant.relevancy import RelevancyChecker
from ai_assistant.conversation_formatter import ConversationFormatter

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)
MESSAGE_STORE = get_message_store()
RELEVANCY_CHECKER = RelevancyChecker()
CONVERSATION_FORMATTER = ConversationFormatter()

app = FastAPI(
    title="AI Assistant API",
    description="AI Assistant with Steam and ClickHouse integration for Discord Bot",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
def redirect_to_docs():
    return RedirectResponse("/scalar", HTTP_308_PERMANENT_REDIRECT)


@app.get("/scalar", include_in_schema=False)
async def scalar_html():
    return get_scalar_api_reference(openapi_url=app.openapi_url, title=app.title, scalar_theme="default")


@app.get("/replay")
async def replay(
    prompt: str = Query(
        ...,
        min_length=1,
        max_length=10000,
        description="The prompt to send to the AI agent",
    ),
    memory_id: UUID | None = Query(None),
    steam_id: int | None = Query(None),
    model: str | None = Query(None, description="Model to use for inference"),
    markdown_syntax: bool | None = Query(False, description="Result Markdown Syntax"),
    sleep_time: str | None = Query(None, description="Sleep time in seconds between messages"),
):
    async def generator():
        for line in REPLAY:
            yield line
            if sleep_time:
                await asyncio.sleep(float(sleep_time))

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class StreamingResponseHandler:
    @staticmethod
    def serialize_step(step) -> Dict[str, Any] | None:
        if isinstance(step, ActionStep):
            return {"type": "action", "data": [m.dict() for m in step.to_messages()]}
        elif isinstance(step, ActionOutput):
            return {
                "type": "action_output",
                "data": step.dict() if hasattr(step, "dict") else str(step),
            }
        elif isinstance(step, PlanningStep):
            return {
                "type": "planning",
                "data": [m.dict() for m in step.to_messages()],
            }
        elif isinstance(step, ChatMessageStreamDelta):
            return {"type": "delta", "data": {"content": step.content}}
        elif isinstance(step, FinalAnswerStep):
            return {"type": "final_answer", "data": step.output}
        return None

    @classmethod
    def generate_stream(
        cls,
        prompt: str,
        steam_id: int | None,
        model: ApiModel,
        memory: AgentMemory | None = None,
        markdown_syntax: bool = False,
    ) -> Generator[str, None]:
        try:
            instructions = f"""
Current Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

{f"Steam ID of Prompting User: {steam_id}" if steam_id else ""}

{AGENT_INSTRUCTIONS}
"""
            agent = CodeAgent(
                model=model,
                tools=ALL_TOOLS,
                instructions=instructions,
            )
            if memory:
                agent.memory = memory
            with agent:
                for step in agent.run(prompt, stream=True, reset=False, max_steps=10):
                    try:
                        serialized = cls.serialize_step(step)
                    except Exception as e:
                        LOGGER.error(f"Error serializing step: {e}")
                        continue
                    if serialized:
                        data = json.dumps(serialized)
                        LOGGER.info(f"Streaming data: {data}")
                        yield f"event: agentStep\ndata: {data}\n\n"
                    else:
                        LOGGER.debug(f"Skipping step: {type(step)}")

            # Generate formatted conversation response using the light model
            try:
                formatted_response = CONVERSATION_FORMATTER.format_conversation(agent.memory, markdown_syntax)
                LOGGER.info(f"Generated formatted conversation response: {formatted_response}")
                formatted_response = {"type": "formatted_response", "data": formatted_response}
                yield f"event: agentStep\ndata: {json.dumps(formatted_response)}\n\n"
                LOGGER.info("Formatted conversation response generated successfully")
            except Exception as e:
                LOGGER.error(f"Error generating formatted response: {e}")
                # Continue without formatted response if it fails

            memory_id = MESSAGE_STORE.save_memory(agent.memory)
            yield f"event: memoryId\ndata: {memory_id}\n\n"
        except Exception as e:
            LOGGER.error(f"Error during agent execution: {e}")
            yield f"event: error\ndata: {e}\n\n"
        yield "FINISHED"


@app.get("/invoke")
async def invoke(
    prompt: str = Query(
        ...,
        min_length=1,
        max_length=10000,
        description="The prompt to send to the AI agent",
    ),
    memory_id: UUID | None = Query(None),
    steam_id: int | None = Query(None),
    model_name: str | None = Query(None, description="Model to use for inference"),
    api_key: str | None = Query(None, description="API-Key"),
    markdown_syntax: bool | None = Query(False, description="Result Markdown Syntax"),
):
    if valid_api_keys := os.environ.get("API_KEYS"):
        if valid_api_keys and api_key not in valid_api_keys.split(","):
            raise HTTPException(status_code=401, detail="Unauthorized")

    if not prompt or not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    if model_name is not None and model_name not in MODEL_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model. Available models: {list(MODEL_CONFIGS.keys())}",
        )

    model = MODEL_CONFIGS[model_name]() if model_name else get_model()

    memory = None
    if memory_id:
        if memory := MESSAGE_STORE.get_memory(memory_id):
            LOGGER.info(f"Loaded memory for ID {memory_id}")
        else:
            LOGGER.warning(f"No memory found for ID {memory_id}, starting fresh.")

    if DO_RELEVANCY_CHECK and not RELEVANCY_CHECKER.is_relevant(prompt.strip(), memory):
        raise HTTPException(
            status_code=400,
            detail="This assistant only handles Deadlock game-related questions. Please ask about Deadlock gameplay, heroes, items, statistics, or other game-related topics.",
        )

    try:
        if model_name and model_name.startswith("gemini"):
            model.api_key = utils.get_gemini_api_key()
        stream = StreamingResponseHandler.generate_stream(prompt.strip(), steam_id, model, memory, markdown_syntax)

        async def async_stream():
            while True:
                item = await asyncio.to_thread(next, stream)
                if item == "FINISHED":
                    break
                yield item

        return StreamingResponse(
            async_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        LOGGER.error(f"Failed to create agent or start streaming: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
