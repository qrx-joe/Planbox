# 参考来源与采用边界

核对日期：2026-10-16。优先使用作者仓库、框架官方文档、标准发布机构资料。下面记录的是资料陈述及本项目拟采用的规则，**没有安装或完整审计这些产品，也没有认证 Planbox 符合全部标准**。

## 1. 受关注的公开项目

关注度仅采用访问时 GitHub 页面显示的约数，可能受页面缓存影响；不是全网热度排行榜，也不证明活跃用户、商业成功或工程质量。

| 来源 | 访问时关注度 | 借鉴内容及 Planbox 的对应规范 | 采用边界 |
| --- | --- | --- | --- |
| [Karakeep 作者仓库](https://github.com/karakeep-app/karakeep) / [官方功能文档](https://docs.karakeep.app/) | 约 29.7k stars | 多入口采集、OCR、归档与阅读；对应统一采集结果、原文回看、处理状态 | 借鉴公开功能与结构，不能据此推断微信/小红书稳定支持；仓库为 AGPL-3.0，复制代码前另核许可证义务 |
| [Linkwarden 作者仓库](https://github.com/linkwarden/linkwarden) | 约 20.1k stars | 网页截图/PDF/HTML 保存、阅读标注、权限；对应来源快照和证据归属 | 不照搬其团队权限或全部部署结构；AGPL-3.0 代码复用需单独审查 |
| [n8n 作者仓库](https://github.com/n8n-io/n8n) / [官方错误处理文档](https://docs.n8n.io/flow-logic/error-handling/) | 约 207.6k stars | 流程执行与错误处理；对应分阶段状态、可观察失败与有限重试 | 属于 fair-code 项目；只借鉴流程思想，不将其引擎或许可直接当作通用开源依赖纳入 |

补充交互参考：[Photocalia 作者仓库](https://github.com/m-idriss/photocalia)描述图片/PDF 转日历与导出前编辑，其转换后端单独运营；适合参考确认体验，不能当作完整可复用后端。它不纳入上面的高关注度对照。

小红书适配候选：[jackwener/xiaohongshu-cli](https://github.com/jackwener/xiaohongshu-cli)、[tamnd/xiaohongshu-cli](https://github.com/tamnd/xiaohongshu-cli)。只作只读适配调研来源，尚未在本项目验证；不能因为工具具有发布、评论或登录能力就自动扩大调用范围。

## 2. 标准与官方工程资料

| 编号 | 主来源 | 本项目采用的验收方向 |
| --- | --- | --- |
| S-01 | [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339) | 已确定时间戳使用明确时区/偏移；日期、未知和相对时间另有语义，不补造精度 |
| S-02 | [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545) | ICS 的 CRLF、转义、UID、日期/时区和 UTF-8 行折叠；通知行为还需单独客户端测试 |
| S-03 | [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) | 新错误接口参考 Problem Details；旧 detail 格式需兼容迁移，不直接全量替换 |
| S-04 | [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html) | URL、实际连接目标、重定向、网络出口与响应限制，不能只做字符串域名包含判断 |
| S-05 | [OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html) | 大小、类型与文件特征校验、受控命名和存储、最小访问范围 |
| S-06 | [WCAG 2.2](https://www.w3.org/TR/WCAG22/) | 以 AA 为目标设计标签、键盘、焦点、错误提示、对比度和目标尺寸；未完成审计不声明全面符合 |
| S-07 | [FastAPI 多文件应用](https://fastapi.tiangolo.com/tutorial/bigger-applications/) | 使用现有 router/依赖机制分职责；不为分层制造网络服务 |
| S-08 | [React 状态结构](https://react.dev/learn/choosing-the-state-structure) | 降低状态矛盾与重复，按实体与查询范围保存页面数据 |
| S-09 | [TypeScript strict](https://www.typescriptlang.org/tsconfig/strict.html) | 保持 strict；外部数据仍要运行时校验，不能仅依赖类型断言 |
| S-10 | [SQLite 外键](https://www.sqlite.org/foreignkeys.html) / [事务](https://www.sqlite.org/lang_transaction.html) | 显式验证连接约束和事务回滚，迁移前检查存量数据 |
| S-11 | [uv 锁定与同步](https://docs.astral.sh/uv/concepts/projects/sync/) | 区分声明范围、锁文件与实际环境；可复现安装，升级显式进行 |
| S-12 | [PEP 257](https://peps.python.org/pep-0257/) | 文档字符串说明行为、参数、返回、异常、副作用和适用限制 |
| S-13 | [Playwright 最佳实践](https://playwright.dev/docs/best-practices) | 测用户可见行为、隔离数据、稳定定位；新增依赖与 CI 另行实施 |

上述内容只选与当前产品直接有关的部分。OWASP 是工程指导，WCAG 有完整符合性要求，RFC 有各自协议范围；不能统称为“通过行业认证”。

## 3. 从参考到实现的记录要求

每次采用外部做法记录：源链接与核对日期 → 原资料明确说明的能力 → 本项目的具体问题 → 自己采用的设计 → 许可证/兼容性影响 → 验证方式 → 未采用的部分。

产品功能描述不等于内部源码证明；公开源码不等于本项目已接入；仓库 stars 不作为选型通过条件。若要复制代码、引入依赖或使用账户能力，分别完成许可证核查、范围审查与适用授权检查。
