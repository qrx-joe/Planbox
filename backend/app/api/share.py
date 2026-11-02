"""小程序分享入口 API。

小程序端调用示例：

```js
// app.js —— 冷启动
App({
  onLaunch(options) {
    // options.scene / options.query 带上分享来源信息
    if (options.query && options.query.scene) {
      wx.setStorageSync('sharePayload', {
        scene: options.scene,
        query: options.query.q || '',
        title: options.query.title || '',
      })
    }
  }
})
```

```js
// pages/inbox/index.js —— 分享唤起后补全正文
Page({
  onLoad() {
    const payload = wx.getStorageSync('sharePayload')
    if (payload) this.setData({ sharePayload: payload })
  },

  async submit() {
    const { data } = await wx.request({
      url: `${BASE}/api/share/intake`,
      method: 'POST',
      data: this.data.sharePayload,
    })
    if (data.state === 'needs_content') {
      // 引导用户回文章页复制正文后粘贴
      this.setData({ pendingId: data.source_item_id, hint: data.next_action })
    }
  },

  async complete(rawText) {
    await wx.request({
      url: `${BASE}/api/share/${this.data.pendingId}/complete`,
      method: 'POST',
      data: { raw_text: rawText, auto_extract: true },
    })
  }
})
```
"""

from fastapi import APIRouter, HTTPException, status

from backend.app.repositories.source_item_repository import SourceStateConflict
from backend.app.schemas.share import (
    ContentCompleteRequest,
    ContentCompleteResponse,
    ShareIntakeRequest,
    ShareIntakeResponse,
)
from backend.app.services.share_ingest_service import ShareIngestService

router = APIRouter()
service = ShareIngestService()


@router.post("/intake", response_model=ShareIntakeResponse)
async def share_intake(payload: ShareIntakeRequest) -> ShareIntakeResponse:
    """接收小程序分享唤起。

    会尽力抓取公众号文章正文；抓不到时返回 state=needs_content
    与明确的下一步指引，**不会报错**——微信对服务端直连有风控，
    这是预期行为而非异常。
    """
    return await service.share_intake(payload)


@router.post("/{source_item_id}/complete", response_model=ContentCompleteResponse)
async def complete_content(
    source_item_id: str, payload: ContentCompleteRequest
) -> ContentCompleteResponse:
    """用户粘贴正文后补全分享入口建立的条目。"""
    try:
        return await service.complete_content(source_item_id, payload)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Source item not found"
        ) from exc
    except SourceStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
