import os
import sys
from pathlib import Path

# The desktop app writes Chinese startup diagnostics on Windows. Force UTF-8
# so English-locale Windows consoles (for example cp1252) cannot abort startup.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

API_ROOT = Path(__file__).resolve().parent
os.chdir(API_ROOT)
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.main import app
import uvicorn

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")