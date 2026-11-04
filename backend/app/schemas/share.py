"""小程序分享入口的接收契约。

微信生态的能力边界（已核对官方文档，2026-10）：
- 小程序**无法**在用户分享文章时被告知「正在读哪篇文章」。
  `wx.openOfficialAccountArticle` 仅在微信鸿蒙 OS 版支持，iOS/安卓不可用。
- 公众号文章里插入小程序卡片只能插在**自己控制的号**里，
  对第三方公众号文章无效。
- 因此正文必须由用户提供，唯一稳定路径是用户复制粘贴。

分享唤起能拿到的可用信息：
- `scene`           小程序场景值，可判断来源（1007/1044等）
- `q`               分享时 path 里的 query 串，可能含用户预置的文章链接
- `title`           转发标题（小程序自定义的，可能就是文章标题）
- `webviewUrl`      仅当来源页面含<web-view> 组件时有值，
                    属于小程序自己的页面地址，不是公众号文章地址

设计成两段式：
  第一段 share-intake  用分享参数建条目，标记 needs_content
  第二段 content-complete 用户粘贴正文后补全，同一 id幂等更新
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# 微信官方场景值中，与「分享」相关的
ShareScene = Literal[
    "1007",  # 分享给朋友
    "1008",  # 分享到群聊
    "1044",  # 分享到朋友圈
    "1155",  # 朋友分享的卡片
    "unknown",
]

# 条目在分享链路中的补全状态
IngestState = Literal["needs_content", "ready"]


class ShareIntakeRequest(BaseModel):
    """小程序分享唤起时提交。

    所有字段都可为空——分享唤起可能什么都拿不到，
    此时只能建立一个待补充的空条目供用户粘贴。
    """

    # 分享来源标识
    scene: str | None = Field(default=None, max_length=8, description="小程序场景值")

    # 分享 path 中的 query 串或其中的文章链接
    query: str | None = Field(default=None, max_length=2000)

    # 转发标题，小程序自定义时通常是活动名或文章标题
    title: str | None = Field(default=None, max_length=300)

    # 分享卡片描述，可能是文章摘要
    summary: str | None = Field(default=None, max_length=1000)

    # 公众号文章链接，仅当用户主动在 path 里预置时才有
    article_url: str | None = Field(default=None, max_length=2000)

    # web-view 页面地址。注意这是小程序自己的页面，不是公众号文章
    webview_url: str | None = Field(default=None, max_length=2000)

    # 分享者的原始素材说明，便于排查
    referer: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def normalize(self) -> "ShareIntakeRequest":
        # 兼容：部分端只传 query，文章链接藏在 query 里
        if not self.article_url and self.query:
            extracted = _extract_article_url(self.query)
            if extracted:
                self.article_url = extracted
        return self


class ShareIntakeResponse(BaseModel):
    """分享入口处理结果。前端据此决定是否提示用户粘贴正文。"""

    source_item_id: str
    state: IngestState
    title: str | None = None
    source_url: str | None = None
    # 是否识别为微信文章链接
    is_wechat_article: bool = False
    # 抓取结果说明
    fetch_attempted: bool = False
    fetch_succeeded: bool = False
    fetch_error: str | None = None
    # 给用户看的人话指引
    next_action: str
    # 是否已经直接触发了 AI 抽取
    extraction_started: bool = False


class ContentCompleteRequest(BaseModel):
    """用户粘贴正文后补全。"""

    raw_text: str = Field(min_length=1, max_length=200_000)
    title: str | None = Field(default=None, max_length=300)
    published_at: str | None = None
    # 是否立即触发 AI 抽取，默认true
    auto_extract: bool = True


class ContentCompleteResponse(BaseModel):
    source_item_id: str
    state: IngestState
    title: str | None = None
    extraction_started: bool = False
    extraction_id: str | None = None
    # 解析终态：parsed=已生成草稿；no_event=未发现活动；failed=抽取失败可重试
    parse_status: str | None = None


def _extract_article_url(text: str) -> str | None:
    """从 query 串或任意文本中提取公众号文章链接。

    小程序分享时如果 path 带参，链接可能被 urlencode 过，
    因此这里同时尝试原始匹配和解码后匹配。
    """
    import re
    from urllib.parse import unquote

    candidates = [text, unquote(text)]
    for candidate in candidates:
        match = re.search(r"https?://mp\.weixin\.qq\.com/[^\s\"'<>]+", candidate)
        if match:
            return match.group(0)
    return None