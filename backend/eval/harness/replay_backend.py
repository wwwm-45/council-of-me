from __future__ import annotations

import json


class ReplayBackend:
    def __init__(self, responses: list[dict]):
        self._responses = list(responses)

    async def generate(self, prompt: str, **kwargs) -> str:
        if not self._responses:
            raise RuntimeError("Replay response queue exhausted")
        item = self._responses.pop(0)
        return str(item["response_text"])

    async def generate_chat(self, messages: list[dict], **kwargs) -> str:
        return await self.generate(prompt=json.dumps(messages, ensure_ascii=False), **kwargs)
