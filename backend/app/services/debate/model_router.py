from collections.abc import Awaitable, Callable

from app.services.llm import generate as llm_generate
from eval.harness.trace_context import push_trace_stage


class ModelRouter:
    def __init__(
        self,
        primary_model: str,
        auxiliary_model: str,
        generate_fn: Callable[..., Awaitable[str]] = llm_generate,
    ) -> None:
        self._models = {
            "primary": primary_model,
            "auxiliary": auxiliary_model,
        }
        self._generate_fn = generate_fn
        self._task_tiers = {
            "agent_statement": "primary",
            "tension_extraction": "primary",
            "engagement_evaluation": "primary",
            "convergence_mapping": "primary",
            "meta_extraction": "auxiliary",
            "consistency_check": "auxiliary",
            "position_extraction": "auxiliary",
            "evolution_tracking": "auxiliary",
        }

    async def generate(
        self,
        *,
        task: str,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        tier = self._task_tiers.get(task, "primary")
        model = self._models[tier]
        with push_trace_stage(task):
            return await self._generate_fn(
                prompt=prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                model=model,
            )
