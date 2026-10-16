# Planbox

Planbox is an early MVP for collecting event sources, reviewing extraction drafts, and exporting confirmed events as ICS files. Goal-based ranking and concrete time planning are being designed.

## 项目规划与协作

- [任务与文档入口](docs/task/README.md)：当前待办、下一步、问题、协作记录、建议与工程规范。
- [下一阶段产品方案](docs/NEXT_PHASE_PLAN.md)：微信/小红书/图片输入、按目标筛选与行动规划。
- [项目协作规则](AGENTS.md)：目录职责、检查入口、修改与提交边界。
- [小程序接入与本地运行](docs/MINIPROGRAM.md)：原生小程序分享预填、正文粘贴与补全入口，源码在 `miniprogram/`。

当前完成情况与历史验证边界见任务入口；规划文档不表示对应功能已经实现。

## Development

Python dependencies must be managed with `uv`.

```bash
uv venv
uv sync
uv run uvicorn backend.app.main:app --reload
```

Frontend dependencies are managed separately under `frontend/`.

```bash
cd frontend
npm install
npm run dev
```

The frontend calls `http://127.0.0.1:8000` by default. To point it elsewhere:

```powershell
$env:VITE_API_BASE_URL = "http://127.0.0.1:8010"
npm run dev
```

On this Windows machine, if `uv` is not on `PATH` yet, add it for the current
PowerShell session:

```powershell
$env:Path = "C:\Users\14536\.local\bin;$env:Path"
$env:UV_CACHE_DIR = "D:\code\Planbox\.uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "D:\code\Planbox\.uv-python"
```

Recommended local URLs:

- Backend: `http://127.0.0.1:8000`
- Frontend: `http://127.0.0.1:5173`
