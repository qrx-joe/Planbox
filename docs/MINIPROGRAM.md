# 小程序分享入口接入指南

## 先读这一段：微信的能力边界

**小程序无法知道用户正在读哪一篇公众号文章。** 这是微信的硬限制，不是实现取舍。已核对官方文档（2026-10）：

| 能力 | 实际状态 |
|---|---|
| `wx.openOfficialAccountArticle` | **仅支持微信鸿蒙 OS 版**，iOS / 安卓不可用 |
| 公众号文章内插小程序卡片 | 只能插在**你自己控制的号**里，对第三方文章无效 |
| `web-view` 打开文章 | 仅限已关联公众号或已配业务域名，且拿不到 DOM |
| 分享唤起时能否拿到文章 URL | **不能**，只有分享者自己预置的 path query |

所以正文必须由用户提供。唯一稳定路径是：**分享唤起拿标题做预填 → 用户复制粘贴正文**。

这条链路本项目后端已完整实现，前端 Web 版也可直接用。

---

## 一、两段式流程

```
第 1 段  分享唤起
         POST /api/share/intake
         └─ 尽力抓取文章正文（大概率失败，属正常）
         └─ 返回 state = needs_content + 下一步指引

第 2 段  粘贴正文
         POST /api/share/{source_item_id}/complete
         └─ 校验字数（至少 50 字）
         └─ 写入正文，触发 AI 抽取
         └─ 返回 extraction_id
```

## 二、接口契约

### `POST /api/share/intake`

所有字段都可为空——分享唤起可能什么都拿不到。

```jsonc
{
  "scene": "1155",              // 小程序场景值
  "query": "id=9&url=...",      // 分享 path 的 query 串
  "title": "活动名或文章标题",   // 转发标题
  "summary": "文章摘要",         // 转发描述
  "article_url": "https://mp.weixin.qq.com/s/xxx",  // 分享者预置的文章链接
  "webview_url": "...",         // 注意：这是小程序自己的页面，不是公众号文章
  "referer": "..."              // 来源标识，便于排查
}
```

响应：

```jsonc
{
  "source_item_id": "src_xxx",
  "state": "needs_content",      // 或 "ready"（正文已足够）
  "is_wechat_article": true,
  "fetch_attempted": true,
  "fetch_succeeded": false,
  "fetch_error": "服务端无法直接抓取该文章正文...",
  "next_action": "分享唤起未携带文章正文。请在微信文章页长按全选复制正文...",
  "extraction_started": false
}
```

**`article_url` 可以省略** —— 若 `query` 里含文章链接（可能是 urlencode 过的），后端会自动提取。

### `POST /api/share/{source_item_id}/complete`

```jsonc
{
  "raw_text": "完整正文...",     // 必填，至少 50 字
  "title": "可选，覆盖标题",
  "auto_extract": true          // 默认 true
}
```

响应：

```jsonc
{
  "source_item_id": "src_xxx",
  "state": "ready",
  "extraction_started": true,
  "extraction_id": "ext_xxx"
}
```

**幂等**：正文完全一致时重复提交返回成功但不重复抽取。

## 三、小程序端代码

### app.js —— 捕获分享参数

```js
App({
  onLaunch(options) {
    this.captureShare(options)
  },

  onShow(options) {
    // 热启动时参数在 onShow，场景值 1155 表示从分享卡片进入
    this.captureShare(options)
  },

  captureShare(options) {
    if (!options || !options.query) return

    const q = options.query.q || ''
    this.globalData.sharePayload = {
      scene: String(options.scene || ''),
      query: q,
      title: options.query.title || '',
      summary: options.query.summary || '',
      article_url: options.query.article_url || '',
    }
  },

  globalData: { sharePayload: null },
})
```

### pages/inbox/index.js —— 完整流程

```js
const BASE = 'https://your-domain.com'

Page({
  data: {
    hint: '',
    pendingId: '',
    rawText: '',
  },

  onLoad() {
    const payload = getApp().globalData.sharePayload
    if (!payload) return

    // 分享标题先预填，正文仍需用户粘贴
    this.setData({
      hint: payload.title || '已接收分享参数',
      rawText: payload.summary || '',
    })
    this.submitShare(payload)
  },

  async submitShare(payload) {
    wx.showLoading({ title: '处理中' })
    try {
      const { data } = await wx.request({
        url: `${BASE}/api/share/intake`,
        method: 'POST',
        data: payload,
      })

      if (data.state === 'needs_content') {
        // 抓不到正文 —— 这是常态，不是错误
        this.setData({
          pendingId: data.source_item_id,
          hint: data.next_action,
        })
      } else {
        wx.showToast({ title: '已生成草稿', icon: 'success' })
        this.fetchReviewQueue()
      }
    } catch (err) {
      wx.showToast({ title: '提交失败', icon: 'none' })
    } finally {
      wx.hideLoading()
    }
  },

  async complete(rawText) {
    if (rawText.trim().length < 50) {
      wx.showToast({ title: '正文太短', icon: 'none' })
      return
    }

    wx.showLoading({ title: '补全中' })
    try {
      const { data } = await wx.request({
        url: `${BASE}/api/share/${this.data.pendingId}/complete`,
        method: 'POST',
        data: { raw_text: rawText, auto_extract: true },
      })
      wx.showToast({ title: '已生成草稿', icon: 'success' })
      this.setData({ pendingId: '' })
      this.fetchReviewQueue()
    } finally {
      wx.hideLoading()
    }
  },
})
```

### 让用户方便粘贴

微信没有「读取剪贴板」的API（`wx.getClipboardData` 在部分场景受限制），推荐做法：

```js
// 用户在按钮上长按 → 触发粘贴
<button bindlongpress="onPaste">长按粘贴正文</button>

onPaste() {
  wx.getClipboardData({
    success: (res) => {
      this.setData({ rawText: res.data })
    },
    fail: () => {
      wx.showToast({ title: '请手动粘贴', icon: 'none' })
    },
  })
}
```

也可以用 `textarea` 的 `adjust-position` 属性，聚焦时避免键盘遮挡。

## 四、微信后台配置清单

| 项目 | 位置 | 说明 |
|---|---|---|
| 合法域名 | 开发 → 开发设置 → 服务器域名 | 填你的 HTTPS 域名，必须已备案 |
| 关联公众号 | 管理 → 关联设置 | 仅当需要 `web-view` 打开文章时才必要 |
| 订阅消息模板 | 功能 → 订阅消息 | 用于报名截止提醒，模板需提前申请 |
| 隐私协议 | 设置 → 用户隐私保护指引 | 上线前必须填写收集用户信息的说明 |

## 五、Web 版前端已支持

打开带参数的地址即可触发预填：

```
http://localhost:5173/?scene=1155&title=活动名&article_url=https://mp.weixin.qq.com/s/xxx
```

Inbox 页会出现「从微信分享进入」面板，抓取失败时出现「需要补全正文」面板，收集列表中未补全的条目显示「待补正文」标记且不可点击抽取。

## 六、遗留的抓取能力

`POST /api/source-items/fetch` 保留但已改为**降级语义**：抓不到时返回 `status=needs_content` 而不是 422 错误，条目照常建立，用户复制的内容有地方安放。

本地开发时该接口可能成功（IP 未被风控），部署到云端后基本会走降级路径——这是预期行为。