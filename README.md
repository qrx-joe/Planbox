# Planbox

Planbox is an AI activity opportunity manager for collecting event posts, extracting structured event data, reviewing evidence, and exporting actionable calendar reminders.

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
