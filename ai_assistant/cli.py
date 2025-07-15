from smolagents import CodeAgent

from ai_assistant.configs import AGENT_INSTRUCTIONS, get_model
from ai_assistant.tools import ALL_TOOLS


def run_agent(prompt: str, model=get_model()):
    agent = CodeAgent(
        model=model,
        tools=ALL_TOOLS,
        instructions=AGENT_INSTRUCTIONS,
    )
    with agent:
        return agent.run(prompt)


if __name__ == "__main__":
    prompt = input("Prompt: ")
    run_agent(prompt)
