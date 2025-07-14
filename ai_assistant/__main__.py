import json
import asyncio
import logging
from typing import Dict, Any, AsyncGenerator
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from scalar_fastapi import get_scalar_api_reference
from smolagents import (
    CodeAgent,
    LiteLLMModel,
    InferenceClientModel,
    ActionStep,
    PlanningStep,
    ChatMessageStreamDelta,
    FinalAnswerStep,
    ActionOutput,
)
from starlette.responses import Response, RedirectResponse
from starlette.status import HTTP_308_PERMANENT_REDIRECT
from starlette.middleware.cors import CORSMiddleware
from ai_assistant.tools import (
    search_steam_profile,
    clickhouse_query,
    hero_name_to_id,
    item_name_to_id,
    rank_to_badge,
)
from ai_assistant.utils import list_clickhouse_tables, schema

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_CONFIGS = {
    "gemini-flash": lambda: LiteLLMModel(model_id="gemini/gemini-2.5-flash"),
    "gemini-pro": lambda: LiteLLMModel(model_id="gemini/gemini-2.5-pro"),
    "ollama": lambda: LiteLLMModel(model_id="ollama/qwen2.5-coder:14b"),
    "hf": lambda: InferenceClientModel(),
}

DEFAULT_MODEL = "hf"


def format_table_schema(table: str) -> str:
    excluded_prefixes = {
        "death_details",
        "max_",
        "book_reward",
        "mid_boss",
        "objectives",
        "personastate",
        "profileurl",
        "avatar",
    }

    columns = [
        f"{name}: {type_}"
        for name, type_ in schema(table).items()
        if not any(name.startswith(prefix) for prefix in excluded_prefixes)
    ]

    return f"## Table: {table}\n" + "\n".join(columns)


TABLES_CONTEXT = "\n\n".join(format_table_schema(table) for table in list_clickhouse_tables())

AGENT_TOOLS = [
    hero_name_to_id,
    item_name_to_id,
    rank_to_badge,
    search_steam_profile,
    clickhouse_query,
]

AGENT_INSTRUCTIONS = f"Available Clickhouse Tables:\n{TABLES_CONTEXT}"


class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, CodeAgent] = {}
        self.locks: Dict[str, asyncio.Lock] = {}

    async def get_agent(self, session_id: str, model_name: str = DEFAULT_MODEL) -> CodeAgent:
        if session_id not in self.sessions:
            if model_name not in MODEL_CONFIGS:
                raise ValueError(f"Unknown model: {model_name}")

            model = MODEL_CONFIGS[model_name]()
            self.sessions[session_id] = CodeAgent(
                model=model,
                tools=AGENT_TOOLS,
                instructions=AGENT_INSTRUCTIONS,
            )
            self.locks[session_id] = asyncio.Lock()

        return self.sessions[session_id]

    async def get_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self.locks:
            self.locks[session_id] = asyncio.Lock()
        return self.locks[session_id]

    def clear_session(self, session_id: str):
        if session_id in self.sessions:
            del self.sessions[session_id]
        if session_id in self.locks:
            del self.locks[session_id]

    def list_sessions(self) -> list:
        return list(self.sessions.keys())


session_manager = SessionManager()

app = FastAPI(
    title="AI Assistant API",
    description="AI Assistant with Steam and ClickHouse integration",
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


@app.get("/health", include_in_schema=False)
def get_health():
    return {"status": "ok", "model": DEFAULT_MODEL}


@app.head("/health", include_in_schema=False)
def get_health_head():
    return Response(status_code=200)


class StreamingResponseHandler:
    @staticmethod
    def serialize_step(step) -> Dict[str, Any]:
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
    async def generate_stream(cls, agent: CodeAgent, prompt: str) -> AsyncGenerator[str, None]:
        try:
            with agent:
                for step in agent.run(prompt, stream=True):
                    serialized = cls.serialize_step(step)
                    if serialized:
                        data = json.dumps(serialized)
                        logger.debug(f"Streaming data: {data}")
                        yield f"data: {data}\n\n"
                        await asyncio.sleep(0)
                    else:
                        logger.debug(f"Skipping step: {type(step)}")
        except Exception as e:
            logger.error(f"Error during agent execution: {e}")
            error_data = json.dumps({"type": "error", "data": {"message": str(e)}})
            yield f"data: {error_data}\n\n"


@app.get("/invoke")
async def invoke(
    prompt: str = Query(
        ...,
        min_length=1,
        max_length=10000,
        description="The prompt to send to the AI agent",
    ),
    session_id: str = Query("default", description="Session ID for conversation context"),
    model: str = Query(DEFAULT_MODEL, description="Model to use for inference"),
):
    if not prompt or not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    if model not in MODEL_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model. Available models: {list(MODEL_CONFIGS.keys())}",
        )

    try:
        agent = await session_manager.get_agent(session_id, model)
        lock = await session_manager.get_lock(session_id)

        async with lock:
            return StreamingResponse(
                StreamingResponseHandler.generate_stream(agent, prompt.strip()),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
    except Exception as e:
        logger.error(f"Failed to create agent or start streaming: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/sessions/{session_id}")
async def clear_session(session_id: str):
    session_manager.clear_session(session_id)
    return {"message": f"Session {session_id} cleared"}


@app.get("/sessions")
async def list_sessions():
    return {"sessions": session_manager.list_sessions()}


@app.get("/models")
def list_models():
    return {"models": list(MODEL_CONFIGS.keys()), "default": DEFAULT_MODEL}


if __name__ == "__main__":
    import uvicorn

    prompt = input("Prompt: ")
    if prompt.strip():

        async def run_agent():
            agent = await session_manager.get_agent("cli")
            lock = await session_manager.get_lock("cli")

            async with lock:
                with agent:
                    agent.run(prompt)

        asyncio.run(run_agent())
    else:
        uvicorn.run(app, host="0.0.0.0", port=8000)
