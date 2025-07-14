import json
from typing import Iterable

import uvicorn
from fastapi import FastAPI
from scalar_fastapi import get_scalar_api_reference
from smolagents import (
    CodeAgent,
    LiteLLMModel,
    InferenceClientModel,
    ActionStep,
    PlanningStep,
    ChatMessageStreamDelta,
    FinalAnswerStep,
    ChatMessage,
    ActionOutput,
)
from starlette.requests import Request
from starlette.responses import Response, RedirectResponse, StreamingResponse
from starlette.status import HTTP_308_PERMANENT_REDIRECT

from ai_assistant.tools import (
    search_steam_profile,
    query,
    hero_name_to_id,
    item_name_to_id,
    rank_to_badge,
)
from ai_assistant.utils import list_clickhouse_tables, schema

model = {
    "gemini": LiteLLMModel(model_id="gemini/gemini-2.5-flash"),
    "hf": InferenceClientModel(),
}["gemini"]

CONTEXT = {table: schema(table) for table in list_clickhouse_tables()}

agent = CodeAgent(
    model=model,
    tools=[
        hero_name_to_id,
        item_name_to_id,
        rank_to_badge,
        search_steam_profile,
        query,
    ],
    instructions=f"Available Clickhouse Tables: {json.dumps(CONTEXT, indent=2)}",
)

app = FastAPI()


@app.middleware("http")
async def cors_handler(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response


@app.get("/", include_in_schema=False)
def redirect_to_docs():
    return RedirectResponse("/scalar", HTTP_308_PERMANENT_REDIRECT)


@app.get("/scalar", include_in_schema=False)
async def scalar_html():
    return get_scalar_api_reference(openapi_url=app.openapi_url, title=app.title, scalar_theme="default")


@app.get("/health", include_in_schema=False)
def get_health():
    return {"status": "ok"}


@app.head("/health", include_in_schema=False)
def get_health_head():
    return {"status": "ok"}


@app.get("/invoke")
def invoke(prompt: str):
    if not prompt:
        return "bad request!", 400

    def messages_to_json(messages: Iterable[ChatMessage]):
        return json.dumps([m.dict() for m in messages])

    def generator():
        with agent:
            for step in agent.run(prompt, stream=True):
                data = None
                if isinstance(step, ActionStep):
                    data = messages_to_json(step.to_messages())
                if isinstance(step, ActionOutput):
                    data = json.dumps(step)
                elif isinstance(step, PlanningStep):
                    data = messages_to_json(step.to_messages())
                elif isinstance(step, ChatMessageStreamDelta):
                    data = json.dumps({"delta": step.content})
                elif isinstance(step, FinalAnswerStep):
                    data = json.dumps(step.output)
                if data:
                    print(f"Sending Data: {data}")
                    yield f"data: {data}\n\n"
                else:
                    print(f"Skipping Data: {step}")

    return StreamingResponse(generator(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
