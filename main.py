import os
import warnings

import requests
import sqlglot
from smolagents import CodeAgent, Tool, InferenceClientModel

HF_TOKEN = os.environ.get("HF_TOKEN")
if HF_TOKEN is None:
    warnings.warn("Provide a HF_TOKEN as environment variable")
    exit(1)


class ClickhouseDB(Tool):
    name = "clickhouse_sql"
    description = (
        "Query the clickhouse DB filled with data about deadlock, with a sql query."
    )
    inputs = {"sql": {"type": "string", "description": "The sql query to perform."}}
    output_type = "object"

    def forward(self, sql: str) -> list[dict]:
        sql = sqlglot.transpile(sql, write="clickhouse")[0]
        results = requests.get(
            "https://api.deadlock-api.com/v1/sql", params={"query": sql}
        ).json()
        if len(results) == 0:
            raise Exception("No results found!")
        return results


agent = CodeAgent(
    model=InferenceClientModel(token=HF_TOKEN),
    tools=[ClickhouseDB()],
)

with agent:
    agent.run("How many matches have been tracked?")
