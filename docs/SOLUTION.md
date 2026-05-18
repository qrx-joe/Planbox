# AI 活动机会管理工具实施方案

## 1. 方案目标

基于 [PRD](./PRD.md)，本方案定义第一版产品的落地路径、系统架构、模块边界、数据流、技术选型和开发顺序。

第一版目标不是做一个全自动爬虫，也不是做一个复杂日历系统，而是先跑通一条稳定链路：

```text
用户输入活动信息
  -> 保存原始证据
  -> AI 结构化抽取
  -> 人工确认
  -> 活动入库
  -> 状态管理
  -> 日历导出或提醒
```

关键原则：

- 采集和抽取解耦。
- 抽取和确认解耦。
- 活动库和日历解耦。
- AI 输出必须可追溯、可修改、可拒绝。
- 第一版用最少自动化验证真实使用价值。

## 2. 总体判断

### 2.1 不建议第一版直接做的事

不建议一开始就做：

- 微信群自动监听。
- 小红书自动批量采集。
- 微信公众号登录态抓取。
- 多人协作。
- 手机 App。
- 复杂推荐算法。

原因很简单：这些看起来很诱人，但会把第一版拖进平台风控、登录态维护、隐私合规、数据清洗和权限系统里。真正的核心风险不是“能不能收集”，而是“收集后能不能可靠变成可行动的日程与机会判断”。

### 2.2 第一版应该押注的事

第一版只押注三件事：

- 信息抽取是否足够准。
- 人工确认是否足够省事。
- 提醒和状态管理是否真的减少错过。

如果这三件事不成立，入口做得再花也只是制造更多待处理垃圾。

## 3. 推荐 MVP 形态

### 3.1 产品形态

第一版建议做成本地优先的 Web 应用：

- 后端：FastAPI。
- 前端：React。
- 数据库：SQLite。
- 文件存储：本地 `data/uploads`。
- 日历：先支持 ICS 导出。
- AI：通过可替换的 LLM Provider 接口调用。

优势：

- 开发快。
- 部署简单。
- 适合个人工具。
- 对隐私更友好。
- 后续可以平滑升级到 PostgreSQL、云端部署、Google Calendar API。

### 3.2 第一版用户入口

必须支持：

- 粘贴文本。
- 粘贴链接。
- 上传截图。
- 手动补充活动。

暂不承诺自动读取微信、小红书正文。对于不可访问链接，系统只保存链接，并提示用户粘贴正文或上传截图。

## 4. 系统架构

```text
Frontend
  - Inbox
  - Review Queue
  - Activities
  - Activity Detail
  - Brief

Backend API
  - Source Item API
  - Extraction API
  - Review API
  - Event API
  - Reminder API
  - Calendar Export API

Domain Services
  - Source Service
  - Extraction Service
  - Evidence Service
  - Event Service
  - Dedup Service
  - Scoring Service
  - Reminder Service
  - Calendar Service

Infrastructure
  - SQLite
  - Local File Storage
  - LLM Provider
  - OCR / Vision Provider
  - Background Job Runner
```

## 5. 模块设计

### 5.1 Source Service

职责：

- 接收用户输入。
- 保存原始文本、链接、截图。
- 识别来源类型。
- 创建待解析记录。

不负责：

- 不直接生成活动。
- 不负责 AI 判断。
- 不负责日历提醒。

核心对象：`SourceItem`。

### 5.2 Extraction Service

职责：

- 将 `SourceItem` 转换为结构化候选活动。
- 调用 OCR 或视觉模型处理图片。
- 调用 LLM 输出严格 JSON。
- 生成字段级证据。
- 标记缺失字段和置信度。

不负责：

- 不直接写入正式活动库。
- 不自动同步日历。
- 不替用户做最终参加决策。

核心对象：`ExtractionResult`、`Evidence`。

### 5.3 Review Service

职责：

- 管理待确认队列。
- 支持用户编辑字段。
- 支持确认、放弃、合并。
- 将确认后的候选结果转成正式 `Event`。

这是第一版最重要的 UX。不要幻想 AI 一步到位，确认流程才是系统可信的关键。

### 5.4 Event Service

职责：

- 管理正式活动。
- 管理活动状态。
- 提供列表、详情、搜索、筛选。
- 维护来源证据和活动之间的关系。

状态流转：

```text
extracted
  -> needs_review
  -> interested
  -> applied
  -> ongoing
  -> done

任意阶段可进入：
  -> rejected
  -> expired
```

### 5.5 Dedup Service

职责：

- 检测重复活动。
- 给出合并建议。
- 处理字段冲突。

第一版只做“提示型去重”，不要做自动合并。

判断依据：

- 标题相似度。
- 报名链接相同。
- 主办方相同。
- 举办时间接近。
- 地点相同。

### 5.6 Scoring Service

职责：

- 根据用户目标和活动字段生成机会评分。
- 给出参加、观望、放弃建议。
- 输出理由。

第一版评分不需要复杂算法，用 LLM 加规则即可。

输入：

- 活动信息。
- 用户偏好。
- 时间成本。
- 紧急程度。

输出：

- `fit_score`
- `urgency_score`
- `portfolio_value`
- `networking_value`
- `learning_value`
- `time_cost`
- `recommendation`
- `reason_to_join`
- `reason_to_skip`

### 5.7 Reminder Service

职责：

- 根据活动状态生成提醒。
- 维护提醒触发时间。
- 支持提醒启用和关闭。

第一版可以只生成提醒记录和 ICS，不必做后台通知推送。

### 5.8 Calendar Service

职责：

- 生成 ICS 文件。
- 将报名截止、活动开始、赛程节点转成日历事件。

原则：

- 只导出 `interested` 和 `applied` 活动。
- 不把 `inbox`、`extracted`、`rejected` 写进日历。
- 日历事件标题要带动作，例如 `报名截止：xxx`，避免用户打开日历后不知道要做什么。

## 6. 数据流

### 6.1 文本录入流程

```text
用户粘贴文本
  -> 创建 SourceItem
  -> 调用 Extraction Service
  -> 生成 ExtractionResult
  -> 进入 Review Queue
  -> 用户确认
  -> 创建 Event
```

### 6.2 截图录入流程

```text
用户上传截图
  -> 保存图片
  -> 创建 SourceItem
  -> 视觉模型/OCR 提取文字
  -> LLM 结构化抽取
  -> 生成字段证据
  -> 进入 Review Queue
```

### 6.3 链接录入流程

```text
用户粘贴链接
  -> 保存 URL
  -> 尝试识别平台
  -> 若可读取正文，保存正文
  -> 若不可读取，提示用户粘贴正文或上传截图
  -> 进入解析流程
```

第一版不要为了“自动读取链接”卡死。链接读取失败是正常状态，不是系统失败。

## 7. 数据库设计

### 7.1 source_items

```sql
CREATE TABLE source_items (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_url TEXT,
  raw_text TEXT,
  title TEXT,
  published_at TEXT,
  captured_at TEXT NOT NULL,
  parse_status TEXT NOT NULL,
  parse_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### 7.2 source_assets

```sql
CREATE TABLE source_assets (
  id TEXT PRIMARY KEY,
  source_item_id TEXT NOT NULL,
  asset_type TEXT NOT NULL,
  file_path TEXT NOT NULL,
  mime_type TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (source_item_id) REFERENCES source_items(id)
);
```

### 7.3 extraction_results

```sql
CREATE TABLE extraction_results (
  id TEXT PRIMARY KEY,
  source_item_id TEXT NOT NULL,
  status TEXT NOT NULL,
  extracted_json TEXT NOT NULL,
  missing_fields TEXT,
  confidence REAL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (source_item_id) REFERENCES source_items(id)
);
```

### 7.4 events

```sql
CREATE TABLE events (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  event_type TEXT,
  organizer TEXT,
  registration_url TEXT,
  registration_start_at TEXT,
  registration_deadline_at TEXT,
  event_start_at TEXT,
  event_end_at TEXT,
  location TEXT,
  is_online INTEGER,
  schedule_json TEXT,
  fee TEXT,
  prize TEXT,
  team_required TEXT,
  audience TEXT,
  status TEXT NOT NULL,
  missing_fields TEXT,
  confidence REAL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### 7.5 event_sources

```sql
CREATE TABLE event_sources (
  event_id TEXT NOT NULL,
  source_item_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (event_id, source_item_id),
  FOREIGN KEY (event_id) REFERENCES events(id),
  FOREIGN KEY (source_item_id) REFERENCES source_items(id)
);
```

### 7.6 evidences

```sql
CREATE TABLE evidences (
  id TEXT PRIMARY KEY,
  event_id TEXT,
  extraction_result_id TEXT,
  source_item_id TEXT NOT NULL,
  field_name TEXT NOT NULL,
  field_value TEXT,
  evidence_text TEXT,
  confidence REAL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (event_id) REFERENCES events(id),
  FOREIGN KEY (extraction_result_id) REFERENCES extraction_results(id),
  FOREIGN KEY (source_item_id) REFERENCES source_items(id)
);
```

### 7.7 opportunity_scores

```sql
CREATE TABLE opportunity_scores (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  fit_score INTEGER,
  urgency_score INTEGER,
  portfolio_value INTEGER,
  networking_value INTEGER,
  learning_value INTEGER,
  time_cost TEXT,
  recommendation TEXT,
  reason_to_join TEXT,
  reason_to_skip TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (event_id) REFERENCES events(id)
);
```

### 7.8 reminders

```sql
CREATE TABLE reminders (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  reminder_type TEXT NOT NULL,
  trigger_at TEXT NOT NULL,
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  calendar_event_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (event_id) REFERENCES events(id)
);
```

### 7.9 user_preferences

```sql
CREATE TABLE user_preferences (
  id TEXT PRIMARY KEY,
  goals TEXT,
  interested_topics TEXT,
  blocked_topics TEXT,
  preferred_locations TEXT,
  online_only INTEGER,
  available_time TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

## 8. API 设计

### 8.1 Source API

```text
POST /api/source-items
GET /api/source-items
GET /api/source-items/{id}
POST /api/source-items/{id}/extract
```

### 8.2 Review API

```text
GET /api/review-queue
GET /api/review-queue/{extraction_result_id}
POST /api/review-queue/{extraction_result_id}/confirm
POST /api/review-queue/{extraction_result_id}/reject
POST /api/review-queue/{extraction_result_id}/merge
```

### 8.3 Event API

```text
GET /api/events
POST /api/events
GET /api/events/{id}
PATCH /api/events/{id}
POST /api/events/{id}/status
GET /api/events/{id}/evidences
POST /api/events/{id}/score
```

### 8.4 Reminder API

```text
GET /api/reminders
POST /api/events/{id}/reminders
PATCH /api/reminders/{id}
DELETE /api/reminders/{id}
```

### 8.5 Calendar API

```text
GET /api/calendar/export.ics
GET /api/events/{id}/calendar.ics
```

## 9. AI 抽取方案

### 9.1 输出策略

LLM 必须输出结构化 JSON，后端使用 Pydantic 校验。

如果字段缺失：

- 字段值为 `null`。
- 写入 `missing_fields`。
- 不允许编造。

如果时间不确定：

- 保留原始表达。
- 标记 `needs_review`。
- 不强行转成日期。

### 9.2 抽取输出示例

```json
{
  "title": "AI Agent 黑客松",
  "event_type": "hackathon",
  "organizer": "示例主办方",
  "registration_url": "https://example.com/apply",
  "registration_start_at": null,
  "registration_deadline_at": "2026-06-01T23:59:00+08:00",
  "event_start_at": "2026-06-10T09:00:00+08:00",
  "event_end_at": "2026-06-12T18:00:00+08:00",
  "location": "上海",
  "is_online": false,
  "schedule": [
    {
      "name": "报名截止",
      "start_at": "2026-06-01T23:59:00+08:00"
    },
    {
      "name": "决赛路演",
      "start_at": "2026-06-12T14:00:00+08:00"
    }
  ],
  "fee": "免费",
  "prize": "奖金和孵化资源",
  "team_required": "2-5人组队",
  "audience": "AI 开发者、产品经理、学生",
  "missing_fields": [],
  "confidence": 0.91,
  "evidences": [
    {
      "field": "registration_deadline_at",
      "value": "2026-06-01T23:59:00+08:00",
      "evidence": "报名截止时间：6月1日 23:59",
      "confidence": 0.95
    }
  ]
}
```

### 9.3 时间处理

时间处理要独立成模块，不要散落在 prompt 或页面代码里。

输入：

- 原始时间表达。
- 文章发布时间。
- 捕获时间。
- 用户时区。

输出：

- 标准 ISO 时间。
- 原始表达。
- 是否需要确认。
- 解析依据。

## 10. 前端页面方案

### 10.1 Inbox

核心任务：快速收集，不打断用户。

布局建议：

- 顶部输入区：文本/链接粘贴。
- 图片上传入口。
- 最近收集列表。
- 每条记录显示解析状态。

状态：

- 未解析。
- 解析中。
- 已解析待确认。
- 解析失败。

### 10.2 Review Queue

核心任务：快速确认 AI 结果。

布局建议：

- 左侧：原文、截图、来源链接。
- 中间：可编辑字段表单。
- 右侧：字段证据、置信度、缺失字段。

操作：

- 确认入库。
- 保存草稿。
- 放弃。
- 合并已有活动。

### 10.3 Activities

核心任务：管理机会池。

默认排序：

- 报名截止时间升序。
- 无截止时间但需要确认的活动置顶提醒。

筛选：

- 状态。
- 类型。
- 线上/线下。
- 城市。
- 评分。
- 是否有缺失字段。

### 10.4 Activity Detail

核心任务：查看证据、做决策、设置行动。

模块：

- 活动基本信息。
- 时间节点。
- 报名信息。
- 原文证据。
- 机会评分。
- 用户笔记。
- 提醒设置。

### 10.5 Brief

核心任务：减少用户每天打开工具后的判断成本。

内容：

- 3 天内截止。
- 今天需要行动。
- 信息缺失。
- 推荐优先报名。
- 已过期待处理。

## 11. 后端工程方案

### 11.1 项目结构

```text
backend/
  app/
    main.py
    api/
      source_items.py
      review.py
      events.py
      reminders.py
      calendar.py
    core/
      config.py
      time.py
      ids.py
    db/
      session.py
      models.py
      migrations/
    schemas/
      source_item.py
      extraction.py
      event.py
      reminder.py
    services/
      source_service.py
      extraction_service.py
      evidence_service.py
      event_service.py
      dedup_service.py
      scoring_service.py
      reminder_service.py
      calendar_service.py
    providers/
      llm.py
      vision.py
      calendar.py
    jobs/
      worker.py
  tests/
frontend/
  src/
    app/
    components/
    pages/
    api/
    types/
docs/
```

### 11.2 Python 开发规范

遵循项目约束：

- 使用 `uv` 管理 Python 依赖。
- 使用项目内 `.venv`。
- 不直接使用 `pip`、`poetry` 或 `pip-tools`。

推荐命令：

```bash
uv venv
uv add fastapi uvicorn sqlalchemy pydantic python-multipart
uv run uvicorn app.main:app --reload
```

### 11.3 后台任务

第一版可以先不用 Celery，避免复杂度。

选择：

- 解析任务短：API 同步返回。
- 解析任务长：使用 FastAPI BackgroundTasks 或轻量队列表。

后续再升级：

- Dramatiq。
- RQ。
- Celery。

## 12. 外部集成策略

### 12.1 微信公众号

第一版：

- 保存链接。
- 支持用户粘贴正文。
- 支持截图上传。

后续：

- 接入 OpenCLI WeChat 下载 Markdown。

### 12.2 小红书

第一版：

- 保存链接。
- 支持用户粘贴正文。
- 支持截图上传。

后续：

- 评估 xiaohongshu-cli、redbook、xhs-mcp。
- 所有非官方读取能力都作为可选适配器，不进入核心链路。

### 12.3 日历

第一版：

- ICS 导出。

后续：

- Google Calendar API。
- Outlook Calendar API。

## 13. 开发里程碑

### Milestone 1：项目骨架

交付：

- 后端 FastAPI 项目。
- 前端 React 项目。
- SQLite 初始化。
- 基础页面路由。

验收：

- 本地能启动前后端。
- 能创建 SourceItem。

### Milestone 2：Inbox 与原文保存

交付：

- 文本/链接录入。
- 图片上传。
- 原文列表。
- 文件本地保存。

验收：

- 用户能保存至少三种来源内容。
- 每条内容可回看原文和上传图片。

### Milestone 3：AI 抽取与待确认

交付：

- LLM Provider。
- 抽取 JSON Schema。
- Pydantic 校验。
- Review Queue 页面。

验收：

- 粘贴一段真实活动文本后能生成待确认活动。
- 缺失字段能被标记。
- 字段证据能展示。

### Milestone 4：活动库与状态管理

交付：

- 确认入库。
- 活动列表。
- 活动详情。
- 状态流转。
- 搜索和筛选。

验收：

- 用户能从待确认创建正式活动。
- 能按截止时间排序。
- 能标记感兴趣、已报名、放弃。

### Milestone 5：提醒与 ICS

交付：

- 根据活动时间生成提醒。
- 导出单个活动 ICS。
- 导出全部待行动活动 ICS。

验收：

- 只导出 `interested` 和 `applied` 活动。
- 日历事件标题和时间正确。

### Milestone 6：机会评分与 Brief

交付：

- 用户偏好配置。
- AI 评分。
- 每日机会简报。

验收：

- 系统能给出参加/观望/放弃建议。
- Brief 能列出即将截止和需要确认的活动。

## 14. 测试策略

### 14.1 单元测试

重点覆盖：

- 时间解析。
- 状态流转。
- ICS 生成。
- JSON Schema 校验。
- 去重候选逻辑。

### 14.2 集成测试

重点覆盖：

- SourceItem 创建到 ExtractionResult。
- ExtractionResult 确认到 Event。
- Event 生成 Reminder。
- Event 导出 ICS。

### 14.3 样本测试集

建立 `tests/fixtures/events/`，收集真实样本：

- 微信公众号正文。
- 小红书笔记正文。
- 海报 OCR 文本。
- 含相对时间的活动。
- 缺少报名截止的活动。
- 重复活动。

不要只用干净样例。真实世界的文本才会暴露系统是否靠谱。

## 15. 风险控制

### 15.1 AI 幻觉

控制方式：

- 严格 JSON Schema。
- 字段级证据。
- 置信度。
- 人工确认。
- 缺失字段不编造。

### 15.2 平台风控

控制方式：

- 第一版不依赖自动爬取。
- 平台适配器作为可选模块。
- 核心链路只依赖用户主动输入。

### 15.3 数据污染

控制方式：

- AI 结果先进 Review Queue。
- 正式活动必须人工确认。
- 日历只导出用户感兴趣或已报名活动。

### 15.4 过度设计

控制方式：

- SQLite 起步。
- ICS 起步。
- 本地文件存储起步。
- 不做多用户权限。
- 不做复杂任务队列。

## 16. 关键决策

### 16.1 主库选择

建议第一版使用自建 SQLite，而不是 Notion/飞书。

理由：

- 字段证据、状态流转、去重合并、提醒生成都需要稳定数据模型。
- Notion/飞书适合原型，但很快会被关系数据和证据链拖垮。
- 自建 SQLite 更利于后续迁移。

### 16.2 日历选择

建议第一版使用 ICS 导出，而不是直接接 Google Calendar。

理由：

- 无需 OAuth。
- 实现简单。
- 可被 Apple Calendar、Google Calendar、Outlook 导入。
- 先验证日历事件模型是否合理。

### 16.3 入口选择

建议第一版优先做 Web 粘贴和截图上传。

理由：

- 覆盖微信、小红书、网页、群聊转发。
- 避免平台限制。
- 实现成本低。

### 16.4 AI 抽取选择

建议封装 Provider，不把具体模型写死。

理由：

- 后续可切换 OpenAI、Azure OpenAI、本地模型或其他供应商。
- 便于测试和降级。

## 17. 下一步执行清单

1. 创建项目骨架。
2. 初始化 Python `.venv` 和 `uv` 依赖。
3. 建立 FastAPI 后端。
4. 建立 React 前端。
5. 实现 SQLite 模型。
6. 实现 SourceItem 文本/链接录入。
7. 实现图片上传。
8. 实现 LLM 抽取接口假实现。
9. 实现 Review Queue。
10. 接真实 LLM Provider。
11. 实现活动确认入库。
12. 实现 ICS 导出。

## 18. 最小可验证版本定义

最小可验证版本只需要做到：

- 用户粘贴一段活动文本。
- 系统生成结构化活动草稿。
- 用户确认后进入活动列表。
- 用户标记为感兴趣。
- 系统导出报名截止日历事件。

只要这条链路跑通，就能开始拿真实活动样本迭代。不要在这之前纠结插件、自动爬取和复杂推荐。
