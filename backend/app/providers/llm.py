from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

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
            "enum": [
                "hackathon", "competition", "salon", "bootcamp", "conference", "workshop", "other"],

        },
        "organizer": {"type": "string", "description": "主办方名称"},
        "registration_url": {"type": "string", "description": "报名链接"},
        "registration_start_at": {
            "type": "string",
            "description": (
                "报名开始：完整日期或带明确时区的 ISO 时刻；"
                "不完整保留原片段，无则 null"
            ),
        },
        "registration_deadline_at": {
            "type": "string",
            "description": (
                "报名截止：完整日期或带明确时区的 ISO 时刻；"
                "不完整保留原片段，无则 null"
            ),
        },
        "event_start_at": {
            "type": "string", "description": "活动开始日期/带时区时刻，无则 null",
        },
        "event_end_at": {
            "type": "string", "description": "活动结束日期/带时区时刻，无则 null",
        },
        "location": {"type": "string",
                     "description": "活动地点（城市名），线上活动设为 '线上'，无则 null"},
        "is_online": {"type": "boolean", "description": "是否线上活动，不确定则 null"},
        "schedule": {
            "type": "array",
            "description": "活动日程节点列表，无则空数组",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "节点名称"},
                    "start_at": {
                        "type": "string", "description": "开始日期或带时区时刻；不完整则原片段",
                    },
                    "end_at": {
                        "type": "string", "description": "结束日期或带时区时刻；不完整则原片段",
                    },
                },
            },
        },
        "fee": {"type": "string", "description": "费用说明，无则 null"},
        "prize": {"type": "string", "description": "奖金/奖品说明，无则 null"},
        "team_required": {"type": "string", "description": "组队要求，无则 null"},
        "audience": {"type": "string", "description": "目标受众，无则 null"},
        "missing_fields": {
            "type": "array",
            "items": {"type": "string"},
            "description": "缺失的重要字段列表",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "抽取置信度 0-1",
        },
    },
    "required": ["title", "missing_fields", "confidence"],
}


SYSTEM_PROMPT = """你是一个活动信息结构化抽取专家。用户会提供一段活动文本
（可能来自微信推文、小红书笔记、活动行、掘金等平台），你需要从中抽取结构化的活动信息。

你必须严格只输出一个 JSON 对象，不要有任何其他文字、markdown 代码块标记或解释。

抽取规则：
1. 只输出纯 JSON，不要 ```json 标记
2. 字段缺失时，值设为 null（不要用空字符串）
3. 不要编造信息，只基于原文抽取
4. 时间保持原文精度：只有完整日期输出 YYYY-MM-DD；有明确时区的时刻才输出带偏移的 ISO 时间。
   缺年份、相对时间或缺时区时保留对应原片段供人工核对，不猜年份或时区。
   报名与举办日期均不能补 23:59、午夜或默认结束时长；未知时间设 null。
5. event_type 可选值：hackathon, competition, salon, bootcamp, conference, workshop, other
6. missing_fields 列出所有原文未提供但对参加活动很重要的字段
7. confidence 表示你对抽取结果的整体信心，0-1之间
8. location 提取城市名即可（如"上海"、"北京"），不要详细地址
9. 如果整段文本不包含任何活动、比赛或报名信息，title 设为空字符串 ""，
   其余字段全部为 null，missing_fields 设为 ["title"]；不要为了完成任务编造活动

注意：
- 如果原文没有明确的报名链接，但提供了 source_url，可以用 source_url 作为 registration_url 候选
- 线上活动 location 设为 "线上"，is_online 设为 true
- 如果文本中完全没有时间信息，所有时间字段设为 null"""


USER_PROMPT_TEMPLATE = """请抽取以下活动信息，输出严格 JSON：

{source_text}

{source_url_block}

输出格式要求：
{schema}"""


def _build_schema_text(schema: dict[str, Any]) -> str:
    """将 JSON schema 转为人类可读的格式说明"""
    lines = ["{"]
    for key, val in schema.get("properties", {}).items():
        desc = val.get("description", "")
        type_name = val.get("type", "any")
        if type_name == "array":
            items = val.get("items", {})
            if items.get("type") == "object":
                type_name = "array of objects"
            else:
                type_name = f"array of {items.get('type', 'any')}"
        lines.append(f'  "{key}": "{type_name}"  // {desc}')
    lines.append("}")
    return "\n".join(lines)


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
        # 最近一次调用的用量，供评估脚本观测；不影响抽取行为
        self.last_usage: dict[str, int] | None = None

    async def extract_event(self, text: str, url: str | None = None) -> ExtractedEventDraft:
        url_block = f"来源链接：{url}" if url else ""
        schema_text = _build_schema_text(EXTRACTION_SCHEMA)
        user_prompt = USER_PROMPT_TEMPLATE.format(
            source_text=text,
            source_url_block=url_block,
            schema=schema_text,
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
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

        self.last_usage = data.get("usage") or None
        content = data["choices"][0]["message"]["content"]
        # 清理可能的 markdown 代码块
        content = content.strip()
        if content.startswith("```"):
            # 提取 ```json ... ``` 中的内容
            lines = content.split("\n")
            # 去掉第一行 ```json 和最后一行 ```
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        parsed = json.loads(content)
        return ExtractedEventDraft.model_validate(parsed)


class FakeLLMProvider(BaseLLMProvider):
    """降级用的假实现，实际使用规则匹配"""

    async def extract_event(self, text: str, url: str | None = None) -> ExtractedEventDraft:
        raise NotImplementedError("FakeLLMProvider.extract_event should not be called directly")
