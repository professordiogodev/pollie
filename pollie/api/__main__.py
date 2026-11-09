"""Run with: python -m pollie.api   (reads API_HOST and API_PORT, defaults 0.0.0.0:8000)."""

import os

import uvicorn

DEFAULT_HOST = "0.0.0.0"  # noqa: S104  (a service has to listen on all interfaces inside a container)

if __name__ == "__main__":
    uvicorn.run(
        "pollie.api.main:app", host=os.environ.get("API_HOST", DEFAULT_HOST), port=int(os.environ.get("API_PORT", "8000"))
    )
