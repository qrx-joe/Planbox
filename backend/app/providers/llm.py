from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Literal

import httpx

from backend.app.schemas.extraction import ExtractedEventDraft


class BaseLLMProvider(ABC):
    @abstractmethod
    async def extract_event(self, text: str, url: str | None = None) -> ExtractedEventDraft:
        raise NotImplementedError


EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "活动标题"},
        "event_type": {
            "type": "string",
            "description": "活动类型",
            "enum": ["hackathon", "competition", "salon", "bootcamp", "conference", "workshop", "other"],
        },
        "organizer": {"type": "string", "description": "主办方名称"},
        "registration_url": {"type": "string", "description": "报名链接"},
        "registration_start_at": {
            "type": "string",
            "description": "报名开始时间，ISO 8601 格式",
        },
        "registration_deadline_at": {
            "type": "string",
            "description": "报名截止时间，ISO 8601 格式",
        },
        "event_start_at": {"type": "string", "description": "活动开始时间，ISO 8601 格式"},
        "event_end_at": {"type": "string", "description": "活动结束时间，ISO 8601 格式"},
        "location": {"type": "string", "description": "活动地点，城市名或线上"},
        "is_online": {"type": "boolean", "description": "是否线上活动"},
        "schedule": {
            "type": "array",
            "description": "活动日程节点",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "节点名称"},
                    "start_at": {"type": "string", "description": "开始时间 ISO 8601"},
                    "end_at": {"type": "string", "description": "结束时间 ISO 8601"},
                },
            },
        },
        "fee": {"type": "string", "description": "费用说明"},
        "prize": {"type": "string", "description": "奖金/奖品说明"},
        "team_required": {"type": "string", "description": "是否需要组队"},
        "audience": {"type": "string", "description": "目标受众"},
        "missing_fields": {
            "type": "array",
            "items": {"type": "string"},
            "description": "缺失字段列表",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "置信度 0-1",
        },
    },
    "required": ["title"],
}


SYSTEM_PROMPT = """你是一个活动信息结构化抽取专家。用户会提供一段活动文本（可能来自微信推文、小红书笔记、活动行、掘金等平台），你需要从中抽取结构化的活动信息。

抽取规则：
1. 只输出 JSON，不要有其他文字
2. 字段缺失时，值设为 null
3. 不要编造信息，只基于原文抽取
4. 时间必须用 ISO 8601 格式（如 2026-06-01T23:59:00+08:00）
5. event_type 可选值：hackathon, competition, salon, bootcamp, conference, workshop, other
6. missing_fields 列出所有原文未提供但你推测可能重要的字段
7. confidence 表示你对抽取结果的信心，0-1之间

注意：
- registration_url 如果原文没有明确的报名链接，可以用 source_url 作为候选
- location 如果是线上活动设为 "线上"
- schedule 中的时间节点要完整保留"""


USER_PROMPT_TEMPLATE = """请抽取以下活动信息：

{source_text}

{source_url_block}"""


class OpenAIProvider(BaseLLMProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def extract_event(self, text: str, url: str | None = None) -> ExtractedEventDraft:
        url_block = f"来源链接：{url}" if url else "无来源链接"
        user_prompt = USER_PROMPT_TEMPLATE.format(
            source_text=text,
            source_url_block=url_block,
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": EXTRACTION_SCHEMA,
            },
            "temperature": 0.1,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return ExtractedEventDraft.model_validate(parsed)


class FakeLLMProvider(BaseLLMProvider):
    """降级用的假实现，实际使用规则匹配"""

    async def extract_event(self, text: str, url: str | None = None) -> ExtractedEventDraft:
        raise NotImplementedError("FakeLLMProvider.extract_event should not be called directly")
