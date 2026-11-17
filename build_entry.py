$ErrorActionPreference = "Stop"

$ROOT = "D:\AI漫剧制作大师"
Set-Location $ROOT

Write-Host "`n========== Windows 安装包构建 ==========" -ForegroundColor Cyan

$API = Join-Path $ROOT "apps\api"
$WEB = Join-Path $ROOT "apps\web"
$DESKTOP = Join-Path $ROOT "apps\desktop"
$DIST = Join-Path $API "dist"

if (!(Test-Path "$API\.venv\Scripts\python.exe")) {
    throw "未找到 Python 虚拟环境：$API\.venv"
}

$PY = "$API\.venv\Scripts\python.exe"

# 1. 安装 Python 依赖
Set-Location $API

& $PY -m pip install --upgrade pip
if (Test-Path "requirements.txt") {
    & $PY -m pip install -r requirements.txt
}

& $PY -m pip install pyinstaller

# 2. 生成打包入口
@'
from app.main import app
import uvicorn

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
