# 公网部署验证报告

验证时间：2026-10-16
线上地址：https://planbox-api.app.workbuddy.host

## 结论

**44 项公网断言全部通过。** 完整业务链路在真实公网环境可用：
采集 → AI 抽取 → 待确认 → 活动入库 → 证据溯源 → 筛选 → 状态流转 → 日历导出 → 分享入口。

复现命令：

```bash
.venv/Scripts/python.exe verify_public.py https://你的域名
```

## 环境确认

线上 `/api/health` 实际返回：

```json
{
  "status": "ok",
  "env": {
    "port": "8000",
    "llm_provider": "fake",
    "data_dir": "/workspace/data",
    "db_path": "/workspace/data/planbox.db",
    "cors_origins": ["http://localhost:5173", "http://127.0.0.1:5173"],
    "max_upload_mb": 10
  }
}
```

`PORT` 已注入、数据目录已重定位到容器内、HTTPS 生效。

## 验证发现并修复的问题

### 1. `save_asset` 方法缩进错乱导致上传 500（严重）

`SourceService.save_asset` 掉出了 class 作用域，成为模块级函数。任何文件上传都会 500。

**成因**：上一轮用 Edit 插入上传大小限制时，`mark_extract_pending` 之后缺一个空行，导致 Edit 的匹配边界错位，把方法声明行一并改到了class 外层。

**为什么本地测试没发现**：原有 121 项断言没覆盖上传接口。

**修复**：恢复缩进，新增 `[13] 文件上传` 测试段（4 项断言）。

### 2. 请求体超限时客户端看到连接重置而非 413

拦截超大上传时，middleware 直接回包但没读请求体，客户端还在往 socket 写数据，触发 TCP RST。表现是 `[Errno 10054] 远程主机强迫关闭了一个现有的连接`，而不是预期的 413。

**修复**：拒绝前用 `_drain_body()` 把请求体读完。这是标准 HTTP 陷阱——**返回错误响应前必须消费完请求体**。

### 3. 腾讯云 WAF 拦截 URL 中的完整外链（重要）

`POST /api/source-items/fetch?source_url=https://example.com` 返回 403 WAF 拦截页，而不是应用层的 400。

精确定位结果：

| 请求 | 结果 |
|---|---|
| `?foo=bar` | 422（应用层） |
| `?source_url=https://mp.weixin.qq.com/s/xxx` | 200（放行） |
| `?source_url=https://example.com` | **403（WAF 拦截）** |
| `/api/events?status=interested` | 200（放行） |
| POST 其他接口带任意查询参数 | 201（放行） |

即：**WAF 只拦截 URL 中出现非白名单完整外链的场景**，判定为开放重定向或 SSRF 特征。这是 CDN 层行为，应用代码无法绕过。

**修复**：把文章链接从查询参数移到**请求体**。

```jsonc
// 之前：会被 WAF 拦
POST /api/source-items/fetch?source_url=https%3A%2F%2Fexample.com

// 现在：正常
POST /api/source-items/fetch
Content-Type: application/json

{ "source_url": "https://example.com/notwechat" }
```

**通用教训**：任何需要传 URL 的接口，都应该把URL 放请求体而不是查询参数。

### 4. 生产环境路径相对 CWD 的隐患

`data_dir` 原本是相对路径 `Path("data")`，实际解析依赖进程 CWD。生产环境用不同方式启动会写到意料之外的目录。

**修复**：锚定到 `PROJECT_ROOT`（由 `__file__` 推导），并加validator 保证相对路径一律转换。

### 5. 其他生产化补齐

- **CORS 白名单可配置**：新增 `CORS_ALLOW_ORIGINS` 环境变量（逗号分隔），替换写死的 localhost
- **请求体大小限制**：middleware 拦 `Content-Length`，service 层再校验实际字节数（防 chunked 绕过）
- **微信抓取超时从配置读取**：默认 30 秒 → 10 秒，云端 IP 被风控时快速失败降级优于长时间挂起
- **健康检查暴露环境信息**：便于排查线上配置是否生效
- **上传接口语义修正**：返回 201 而非 200

## 新增文件

| 文件 | 用途 |
|---|---|
| `serve.py` | 生产启动入口，监听 `PORT` 环境变量并绑定 `0.0.0.0` |
| `requirements.txt` | 部署平台依赖清单，与 `pyproject.toml` 对齐 |
| `verify_public.py` | 44 项公网验证脚本，可对任意环境复现 |
| `.dockerignore` | 排除虚拟环境、缓存、测试数据 |

## 遗留限制

1. **LLM provider 为 fake** — 线上未配置 `OPENAI_API_KEY`，抽取走的是规则降级逻辑。真实抽取需要设置环境变量。
2. **无鉴权** — 接口无任何身份校验，多用户场景下数据不隔离。小程序化时必须补`user_id`。
3. **数据不持久** — SQLite 写在容器内`/workspace/data`，容器重建后数据丢失。演示验证足够，生产需换托管数据库。
4. **WAF 规则不可控** — 域名级 WAF 规则由平台维护，若未来新增接口传 URL，仍可能撞上同类拦截。

## 测试覆盖

```
本地端到端：126 项断言（test_e2e.py），可重复执行
公网验证：  44 项断言（verify_public.py）
前端：      tsc 无错误 + vite build 成功
规范：      ruff 全绿
```