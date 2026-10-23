# 人工更正与撤回契约（T-003）

更新：2026-10-23。当前已实现正式活动与 Review 草稿编辑/撤回的受控验证；图片位置证据等仍未完成。主业务库本轮没有启动或迁移。

## 输入、主体与来源

- 保留 `PATCH /api/events/{id}`。字段更正仍使用原字段名；新增可选 `correction_reason`、`source_item_id`、`expected_revision`、`expected_values`。
- 未提供原因时保留 null，不生成虚构解释。`source_item_id` 必须属于活动已有来源；未提供表示更正未绑定来源，不表示主办方已确认。
- actor 固定为 `local_user_unverified`，代表本地手动编辑入口，**不是真实身份认证**；不接受客户端自报 actor 作为认证依据。
- 有变化的字段逐项保存旧值、新值、时间、来源和 operation_id；同一 PATCH 的字段共享 operation_id。null、布尔、赛程 JSON 保留类型，时间保持原精度。状态字段仍由现有状态路径处理，不进入可撤回事实历史；统一状态机留在 T-004。
- `expected_values` 是编辑时读取的原值，`expected_revision` 是活动历史条目总数。新 UI 应同时发送；旧调用不提供时仍记录历史，但无法证明客户端未陈旧。字段不变不新增历史。

## 写入与错误

更正先获取 `BEGIN IMMEDIATE` 短事务，在同一连接内读取、核验预期值/历史版本、校验来源与完整时间区间，再写字段和历史。任一步异常均回滚。错误不包含用户原文或完整异常栈。

- 来源不属于活动、区间不成立、空标题等返回可读 400；请求 schema 错误使用既有 422。
- 陈旧的预期值/版本、无法撤回的历史返回 409；不存在的活动或跨活动撤回返回 404。
- 数据库故障保持失败，不伪装成功；已测试历史写入失败时字段与状态也不落库。

## 历史读取与撤回

`GET /api/events/{id}/corrections?limit=50&offset=0` 返回 items、total、revision；limit 为 1–100，按内部写入序号倒序。实现当前为同一读事务读取后分页，超大历史的性能未测量。

每条记录补充 operation_field_count，表示同次保存的字段总数。Web 分页未加载齐时不提供整次撤回，防止用户只看到部分字段便恢复整次操作。

`POST /api/events/{id}/corrections/{operation_id}/retract` 接受非空 reason。整次操作一并撤回，追加反向历史和 reverts_id，不删除旧记录。任一字段已被后续修改（含改回相同值的 ABA）或当前值不同，整次拒绝；恢复的区间须满足当前契约，不能部分恢复。已成功撤回的相同请求重试只返回当前活动，不覆盖后续更正。撤回记录本身不可再撤回，可发起新的明确更正。

合并时历史随活动归属迁移，original_event_id 保留；迁入历史只读，不允许把原活动旧值直接恢复到保留活动。完整合并事务仍是 T-005 的任务。

## 迁移、删除与回滚边界

- 初始化在现有表结构之后运行 `0001_event_corrections`，原活动/来源不回填；新增 event_corrections、相关索引和 schema_migrations。旧的无版本补列逻辑仍保留，不能据此声称全部历史迁移已有版本。
- `0002_draft_corrections` 仅新增草稿审计与索引，保留原抽取 JSON 和来源；已经安装 0001 的库可以增量应用。失败时 0002 的表与版本记录回滚，0001 和旧数据保留，可修正故障后重试。
- 新版本表与版本记录同事务创建；重复初始化不重复建表。注入迁移失败时新表与版本一并回滚，旧活动保持不变。
- 审计表保留原实体 ID，不建立会级联删除历史的外键。原有删除活动操作不删除更正审计；删除后普通活动接口返回 404，审计数据仍在库中。此行为不能宣称为彻底数据擦除；数据保留与完整删除流程仍待定义。
- 实际数据应用前应备份 SQLite 与原文资产；本轮只运行临时库。旧应用代码可忽略新增表，回滚代码不要求删除历史表；不要通过 DROP TABLE 丢弃更正记录。

## 本轮验证范围

新增回归首先 15 项失败，再实现后通过；最终扩展到 21 项，覆盖逐字段/原子更正、旧客户端、空更正、原子撤回、重试、ABA、陈旧版本、并发编辑唯一成功、实体/来源隔离、历史故障回滚、时间区间、JSON 类型、分页、合并归属、旧赛程损坏/更正保留原值、迁移幂等与失败回滚。

Web 实际验证了证据按活动归属、响应乱序、保存/重开/撤回、赛程增删、非法日期、陈旧版本、断网保留输入及证据/历史失败反馈；9 项前端纯规则回归、构建和 T-003 的 36 项后端回归通过。Playwright/Chrome 与合成库运行，无外部抓取或真实模型；桌面/390px 图片已检查。

不证明真实用户满意、来源语义正确、图片位置正确或通知送达；图片位置及其他任务仍未完成，T-003 仍为 doing。

## Review 草稿编辑与确认

- `GET /api/review-queue/{id}` 重新读取已存候选与 revision；已确认/放弃的记录仍可只读访问。
- `PATCH /api/review-queue/{id}` 接受 changes（只含事实字段和赛程）、必填 expected_revision、可选 expected_values 与 reason。不允许自报状态、置信度、证据、时间核验结果或来源身份。来源固定为此草稿原始素材，actor 为未认证的本地编辑入口。
- `GET /api/review-queue/{id}/corrections` 沿用分页、操作字段数与 revision；`POST .../corrections/{operation_id}/retract` 整次撤回并追加反向记录。已处理的草稿不可编辑或撤回；不同草稿之间的更正 ID 不可互用。
- 草稿可保留相对、缺年或非法日期供后续核对，不自动推断年份/时区；重算 time_issues 和 missing_fields，保存不代表时间有效。撤回可恢复有问题的旧候选，确认仍拒绝未解决问题。
- 当前候选与旧候选的正文依据都保留；证据读取新增 matches_draft/differs_from_draft/no_draft_value，不把旧候选显示成当前依据。“可定位”依旧只表示原文位置存在。
- 比较旧值使用读取 schema 的默认值；旧 JSON 没有 schedule 时，读取和预期值比较统一为默认空列表，避免首次编辑赛程误判冲突。非空的原始日期内容不改写。

新 Web 在编辑打开时禁用确认/放弃，保存后收起编辑，再提交 expected_revision 确认。入库后可从活动依据中的抽取关联回看只读草稿历史。此流程尚未将“核对事实”和“决定参加”拆成两个独立选择（T-006）。

Confirm 使用同一 `BEGIN IMMEDIATE` 连接完成版本检查、兼容旧确认时更正的审计、活动创建、来源关联、证据挂接与状态更新；任一步失败均回滚。旧请求可省略 expected_revision，但不能证明客户端未陈旧。重复确认保留既有 400，未提供幂等结果回放；响应结果未知仍须先检查活动库。合并全事务、外键及其他状态规则仍属 T-004/T-005。

## 复现受控 Web 流程

确认 8023 与 5176 无其他进程占用。先构建默认 API 地址为 `http://127.0.0.1:8000` 的前端；浏览器脚本将其请求拦截转发到自身 8023 合成服务，不请求实际 8000 服务。不要将此测试指向用户库。

```powershell
npm.cmd --prefix frontend run build
# 独立终端 1：只创建 .testdata/t003-browser-* 合成库，启动前打印实际路径。
uv run --frozen --no-sync python tests/fixtures/activity_details_server.py
# 独立终端 2：只读提供构建资源。
.venv/Scripts/python.exe -m http.server 5176 --bind 127.0.0.1 --directory frontend/dist
# 独立终端 3：使用已安装的 Playwright 和 Chrome，无安装或外部联网。
# PLANBOX_PLAYWRIGHT_PATH 如需设置，指向本机实际已有模块；不写进产品配置。
node tests/activity-details.browser.cjs
node tests/review.browser.cjs
```

`--no-sync` 仅适用于已核验环境，本轮既有 Python 元信息差异仍未重建。脚本依赖本机已有 Playwright/Chrome；测试结束关闭自身终端服务。强制中断可能遗留已打印的合成目录，先核对绝对路径与归属再清理；截图保留在忽略的 `.testdata/t003-ui-artifacts/`。
