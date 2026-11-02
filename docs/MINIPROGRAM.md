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

本项目提供这条链路的后端接口、Web 入口，以及 `miniprogram/` 下的原生小程序入口。小程序当前只负责收集和补全；Review 仍在 Web 版完成。

---

## 一、两段式流程

```
第 1 段  分享唤起
         POST /api/share/intake
         └─ 若有文章链接，尽力抓取；失败时仍保存待补全条目
         └─ 返回 state = needs_content + 下一步指引

第 2 段  粘贴正文
         POST /api/share/{source_item_id}/complete
         └─ 校验字数（至少 50 字）
         └─ 写入正文，按 auto_extract 尝试抽取
         └─ 返回 extraction_started；成功触发时可能返回 extraction_id
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

**幂等与重试**：正文、标题和发布时间不变时保留解析状态；已解析的重复提交返回 `extraction_started=false` 和已有 `extraction_id`（可能已确认或放弃，不会重新打开草稿）。尚未解析或上次失败时，`auto_extract=true` 会尝试抽取；`false` 只保存。处理中相同输入不重复抽取，改变输入返回 HTTP 409；抽取结束后改变输入可生成新候选，旧草稿保留。

`state=ready` 仅表示正文已保存。两接口都可能返回 `extraction_started=false`；不能仅凭 `ready` 或 intake 的 `next_action` 宣称生成了草稿。补全请求的正文上限为 200000 字，标题上限为 300 字；噪声清理后不足 50 字返回 HTTP 400，输入格式错误返回 422，条目不存在返回 404。

## 三、小程序端代码

源码：[miniprogram](../miniprogram/)。使用原生 JS/WXML/WXSS，无 npm 运行依赖，无云开发或插件配置。

```text
miniprogram/
  app.js / app.json / app.wxss       冷、热启动参数捕获与应用配置
  project.config.json / sitemap.json 开发者工具导入与搜索配置
  config.js                          本地 API 地址与 60000 毫秒超时
  utils/share.js                     分享参数映射与容错解码
  utils/api.js                       两个 POST、HTTP/网络错误处理
  pages/inbox/index.{js,json,wxml,wxss} 收集与正文补全页面
```

### 本地启动

1. 从仓库根启动后端。默认配置使用 fake Provider；如本地 `.env` 已配置真实模型，则补全会按该配置调用模型。

   ```powershell
   uv sync --locked
   uv run --locked python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
   ```

2. 微信开发者工具导入 **`miniprogram/` 目录**。共享 `project.config.json` 的 `appid` 留空：导入时由用户选择工具提供的测试号或填写自己的真实 AppID，不在仓库编造 AppID。
3. [config.js](../miniprogram/config.js) 默认指向 `http://127.0.0.1:8000`，适用于本机开发者工具。在工具「详情 → 本地设置」临时关闭合法域名/TLS/HTTPS 校验才能访问这个本地 HTTP 地址。共享配置保留 `urlCheck=true`；个人工具配置已忽略，不提交。
4. 真机的 `127.0.0.1` 指向手机自身，不能沿用本机地址。真机/发布前须由用户配置实际可达的服务地址和自己的合法 HTTPS 服务器域名；本仓库未配置线上域名。

### 手动验收

在开发者工具新增编译条件：启动页 `pages/inbox/index`，query 可使用以下**合成预填参数**，场景值用工具实际选择值。

```text
title=Planbox%E5%90%88%E6%88%90%E6%B4%BB%E5%8A%A8&summary=%E5%BE%85%E8%A1%A5%E6%AD%A3%E6%96%87
```

文章链接字段支持 `article_url` 或 `url`，也支持 `q` 内嵌 URL（后端解析）。`scene` 转字符串传给后端，`webview_url`/`webviewUrl` 只保留来源信息，不能当文章链接。平台不会自动附送文章标题，这些都是分享者预置的 query 字段。参数长度不符时按接口 422 提示处理，不悄悄截断原始内容。

- 冷启动/热启动预填标题、链接和摘要，摘要单独展示，不填入正文框；**不会自动 POST**。普通切回前台保留当前输入。新分享到来时保留旧正文，点击「载入新的分享」才切换；未提交输入清空前弹出确认。
- 可直接打开空页面手动收集。点击「收集并继续」建立条目；`needs_content` 显示服务端指引，再长按 `textarea` 粘贴正文。也可先粘贴，再点一次串行完成建档与补全。未调用剪贴板读取 API。
- 输入不足 50 字先阻止提交；服务端清理噪声后仍可返回 400。正在提交时禁用按钮，成功后锁定，避免连点重复创建；「收集另一篇」清空本页并开始新条目。
- HTTP 400/404/422/5xx、网络超时、域名拒绝和异常响应显示在页面内；失败保留正文及已知条目 ID。请求不自动重试，补全的手动重试只发同一 ID 的 complete。
- intake 没有幂等建档承诺；断网时结果可能未知，先去 Web 收集箱核对再重试。正文和条目 ID 仅在当前页面内存保留，关闭/重新编译后的恢复尚未实现。
- `ready` 显示「正文已保存」，另按 `extraction_started` 告知是否触发抽取；展示可获得的 ID，提示用户去 Web Review 核对。不把重复正文或抽取失败写成「已生成草稿」。

### 定向回归

从仓库根运行，无额外依赖、无真实模型调用：

```powershell
node --test tests/miniprogram.test.cjs
```

该测试运行真实 App/Page/API JS，替换微信宿主以覆盖参数、提交顺序、错误反馈和输入保留；不代表微信运行时编译、真机剪贴板、键盘遮挡或真实模型质量已验证。具体本轮结果见 [协作记录](task/communicating.md)。请求使用回调封装 Promise 并检查 HTTP 状态，参考 [微信官方 API 类型定义](https://github.com/wechat-miniprogram/api-typings/blob/master/types/wx/lib.wx.api.d.ts)；生命周期参考 [App 官方定义](https://github.com/wechat-miniprogram/api-typings/blob/master/types/wx/lib.wx.app.d.ts)。

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
