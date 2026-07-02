"""
Thin wrapper interface around AG2 ConversableAgent.
Framework-agnostic abstract interface + AG2 concrete implementation.
AG2 is used as a state container; LLM calls go through our own llm.py.
"""
from abc import ABC, abstractmethod
from typing import Any

from app.services.llm import generate as llm_generate
from app.utils.text import strip_markdown


class DebateAgentInterface(ABC):
    """Framework-agnostic interface for a debate agent."""

    @abstractmethod
    async def generate_statement(self, prompt: str) -> str:
        """Generate a statement given a prompt."""
        ...

    @abstractmethod
    def get_history(self) -> list[dict]:
        """Return the agent's conversation history."""
        ...

    @abstractmethod
    def append_to_history(self, role: str, content: str) -> None:
        """Append a message to the agent's history."""
        ...

    @abstractmethod
    def update_system_prompt(self, new_prompt: str) -> None:
        """Update the agent's system prompt."""
        ...

    @abstractmethod
    def get_identity_card(self) -> dict[str, Any]:
        """Return the full identity card dict."""
        ...

    @abstractmethod
    def get_agent_id(self) -> str:
        ...

    @abstractmethod
    def get_display_name(self) -> str:
        ...

    @abstractmethod
    def get_temperature(self) -> float:
        ...


class AG2DebateAgent(DebateAgentInterface):
    """
    Concrete implementation wrapping AG2 ConversableAgent.
    The ConversableAgent holds identity/state; actual LLM calls
    go through our app.services.llm to preserve Claude routing,
    mock responses, and error handling.
    """

    def __init__(self, identity_card: dict[str, Any], llm_config: dict[str, Any]):
        self._identity_card = identity_card
        self._agent_id: str = identity_card.get("agent_id", "")
        self._display_name: str = identity_card.get(
            "display_name", identity_card.get("role", "")
        )
        self._temperature: float = identity_card.get("temperature", 0.7)
        self._system_prompt: str = identity_card.get("system_prompt", "")
        self._history: list[dict[str, str]] = []

        # AG2 ConversableAgent as state container
        try:
            from autogen import ConversableAgent

            self._ag2_agent = ConversableAgent(
                name=self._agent_id,
                system_message=self._system_prompt,
                llm_config=llm_config,
                human_input_mode="NEVER",
            )
        except ImportError:
            # Graceful degradation: AG2 not installed (e.g. in tests)
            self._ag2_agent = None

    async def generate_statement(self, prompt: str) -> str:
        """Generate via our LLM abstraction, record in history."""
        content = await llm_generate(
            prompt,
            system=self._system_prompt,
            temperature=self._temperature,
        )
        content = strip_markdown(content)
        self.append_to_history("user", prompt)
        self.append_to_history("assistant", content)
        return content

    def get_history(self) -> list[dict]:
        return list(self._history)

    def append_to_history(self, role: str, content: str) -> None:
        self._history.append({"role": role, "content": content})

    def update_system_prompt(self, new_prompt: str) -> None:
        self._system_prompt = new_prompt
        if self._ag2_agent is not None:
            self._ag2_agent.update_system_message(new_prompt)

    def get_identity_card(self) -> dict[str, Any]:
        return self._identity_card

    def get_agent_id(self) -> str:
        return self._agent_id

    def get_display_name(self) -> str:
        return self._display_name

    def get_temperature(self) -> float:
        return self._temperature
