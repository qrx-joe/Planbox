"""生产环境启动入口。

部署平台会注入 PORT 环境变量，服务必须监听该端口并绑定 0.0.0.0，
否则反向代理连不上。

本地直接运行：python serve.py
"""

import os

import uvicorn

from backend.app.main import app


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104 —— 容器环境必须监听全部网卡
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info"),
        # 反向代理后不要信任所有上游头，除非明确知道代理拓扑
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()