import os
import warnings

import requests
import sqlglot
from smolagents import CodeAgent, InferenceClientModel, tool

HF_TOKEN = os.environ.get("HF_TOKEN")
if HF_TOKEN is None:
    warnings.warn("Provide a HF_TOKEN as environment variable")
    exit(1)

@tool
def clickhouse_sql(sql: str) -> list[dict]:
    """
    Query the clickhouse DB containing data about deadlock using a sql query.

    Args:
        sql: The query to perform. This should be correct Clickhouse SQL.
    """
    sql = sqlglot.transpile(sql, write="clickhouse")[0]
    results = requests.get(
        "https://api.deadlock-api.com/v1/sql", params={"query": sql}
    ).json()
    if len(results) == 0:
        raise Exception("No results found!")
    return results

agent = CodeAgent(
    model=InferenceClientModel(token=HF_TOKEN),
    tools=[clickhouse_sql],
)

with agent:
    agent.run("How many matches have been tracked?")
