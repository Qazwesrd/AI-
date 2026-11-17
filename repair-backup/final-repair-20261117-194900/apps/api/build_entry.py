import os
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parent
os.chdir(API_ROOT)
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.main import app
import uvicorn

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")