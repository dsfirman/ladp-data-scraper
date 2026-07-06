import os
import subprocess

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["PYTHONUNBUFFERED"] = "1"

import uvicorn

PORT = 8001


def _kill_process_on_port(port: int) -> None:
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1]
                subprocess.run(
                    ["taskkill", "/PID", pid, "/F"],
                    capture_output=True,
                    timeout=5,
                )
                print(f"Killed process {pid} on port {port}")
    except Exception:
        pass


if __name__ == "__main__":
    _kill_process_on_port(PORT)
    print(f"Server running at: http://localhost:{PORT}/api/v1/scrape")
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=True, log_level="debug")
