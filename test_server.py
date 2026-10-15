"""启动指向独立测试库的 API 服务，供 test_e2e.py 使用。

与 test_e2e.py 共用同一个库文件，这样测试脚本内的进程内断言
（直接调 DedupService）与HTTP 接口看到的是同一份数据。
不会污染 data/planbox.db。
"""

import os
import pathlib

TEST_DIR = pathlib.Path(__file__).parent / ".testdata"
TEST_DIR.mkdir(exist_ok=True)

os.environ["database_path"] = str(TEST_DIR / "e2e.db")
os.environ["data_dir"] = str(TEST_DIR)
os.environ["upload_dir"] = str(TEST_DIR / "uploads")
os.environ["llm_provider"] = "fake"

from backend.app.db.database import initialize_database  # noqa: E402

if __name__ == "__main__":
    # 每次启动前清库，保证测试可重复执行
    db = TEST_DIR / "e2e.db"
    if db.exists():
        db.unlink()

    initialize_database()

    import uvicorn

    from backend.app.main import app

    uvicorn.run(app, host="127.0.0.1", port=8020, log_level="warning")