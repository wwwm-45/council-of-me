"""
Factory: identity card → AG2DebateAgent.
Builds LLM config from app.config and creates agents with per-agent temperature.
"""
from typing import Any, Optional

from app import config
from app.services.debate.agent_adapter import AG2DebateAgent
from app.services.debate.blackboard import AgentState


def build_llm_config() -> dict[str, Any]:
    """Convert app.config LLM settings into AG2 config_list format."""
    config_entry: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "api_key": config.LLM_API_KEY or "mock-key",
    }
    if config.LLM_BASE_URL:
        config_entry["base_url"] = config.LLM_BASE_URL
    return {
        "config_list": [config_entry],
        "temperature": config.LLM_TEMPERATURE_DEFAULT,
    }


def create_debate_agent(
    identity_card: dict[str, Any],
    llm_config: Optional[dict[str, Any]] = None,
) -> AG2DebateAgent:
    """Create a single debate agent from an identity card."""
    if llm_config is None:
        llm_config = build_llm_config()
    # Override temperature from identity card
    agent_config = dict(llm_config)
    agent_config["temperature"] = identity_card.get("temperature", 0.7)
    return AG2DebateAgent(identity_card=identity_card, llm_config=agent_config)


def create_all_agents(
    identity_cards: list[dict[str, Any]],
    llm_config: Optional[dict[str, Any]] = None,
) -> list[AG2DebateAgent]:
    """Create debate agents for all identity cards."""
    if llm_config is None:
        llm_config = build_llm_config()
    return [create_debate_agent(card, llm_config) for card in identity_cards]


def build_agent_states(identity_cards: list[dict[str, Any]]) -> dict[str, AgentState]:
    """Create blackboard-facing agent state shells from identity cards."""
    states: dict[str, AgentState] = {}
    for card in identity_cards:
        agent_id = card.get("agent_id", "")
        states[agent_id] = AgentState(
            agent_id=agent_id,
            agent_name=card.get("display_name", agent_id),
            identity_card=card,
        )
    return states
