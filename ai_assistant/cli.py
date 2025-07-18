from smolagents import CodeAgent

from ai_assistant.configs import AGENT_INSTRUCTIONS, get_model
from ai_assistant.tools import ALL_TOOLS
from ai_assistant.relevancy import RelevancyChecker


def run_agent(prompt: str, model=get_model()):
    relevancy_checker = RelevancyChecker()
    if not relevancy_checker.is_relevant(prompt):
        print(
            "This assistant only handles Deadlock game-related questions. Please ask about Deadlock gameplay, heroes, items, statistics, or other game-related topics."
        )
        return None

    agent = CodeAgent(
        model=model,
        tools=ALL_TOOLS,
        instructions=AGENT_INSTRUCTIONS,
    )
    with agent:
        return agent.run(prompt)


if __name__ == "__main__":
    prompt = input("Prompt: ")
    result = run_agent(prompt)
    if result:
        print(result)
