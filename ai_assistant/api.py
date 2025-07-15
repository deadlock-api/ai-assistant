import asyncio
import json
import logging
import os
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
)
from starlette.responses import RedirectResponse
from starlette.status import HTTP_308_PERMANENT_REDIRECT
from starlette.middleware.cors import CORSMiddleware

from ai_assistant.configs import MODEL_CONFIGS, AGENT_INSTRUCTIONS, get_model, get_message_store, REPLAY
from ai_assistant.tools import ALL_TOOLS

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)
MESSAGE_STORE = get_message_store()

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
    model: str | None = Query(None, description="Model to use for inference"),
    sleep_time: int | None = Query(None, description="Sleep time in seconds between messages"),
):
    async def generator():
        for line in REPLAY:
            yield line
            if sleep_time:
                await asyncio.sleep(sleep_time)

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
        model: ApiModel,
        memory_id: UUID | None = None,
    ) -> Generator[str, None]:
        try:
            agent = CodeAgent(
                model=model,
                tools=ALL_TOOLS,
                instructions=AGENT_INSTRUCTIONS,
            )
            if memory_id:
                if memory := MESSAGE_STORE.get_memory(memory_id):
                    agent.memory = memory
            with agent:
                for step in agent.run(prompt, stream=True):
                    serialized = cls.serialize_step(step)
                    if serialized:
                        data = json.dumps(serialized)
                        LOGGER.debug(f"Streaming data: {data}")
                        yield f"event: agentStep\ndata: {data}\n\n"
                    else:
                        LOGGER.debug(f"Skipping step: {type(step)}")
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
    model: str | None = Query(None, description="Model to use for inference"),
    api_key: UUID | None = Query(None, description="API-Key"),
):
    if valid_api_keys := os.environ.get("API_KEYS"):
        if valid_api_keys and str(api_key) not in valid_api_keys.split(","):
            raise HTTPException(status_code=401, detail="Unauthorized")

    if not prompt or not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    if model is not None and model not in MODEL_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model. Available models: {list(MODEL_CONFIGS.keys())}",
        )
    model = MODEL_CONFIGS[model]() if model else get_model()

    try:
        stream = StreamingResponseHandler.generate_stream(prompt.strip(), model, memory_id)

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
