from ...llm.api_provider import *
from openai import AsyncOpenAI
import asyncio
import json
import os


class NewApiApiProvider(ApiProvider):
    def __init__(self, name: str = "new-api", code: str = "na"):
        super().__init__(name=name, code=code)

    def get_client(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=self.get_api_key(),
            base_url=self.get_base_url(),
        )

    async def sync_quota(self):
        return None



