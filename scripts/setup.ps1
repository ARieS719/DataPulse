# DataPulse 一键环境搭建（在 VS Code 终端执行）：
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# 前置要求：
#   - Python 3.10-3.12（https://www.python.org/downloads/，安装时勾选 Add to PATH）
#   - Java 8/11/17（PySpark 运行必需；推荐 Temurin 17：https://adoptium.net/）
#   - 可选 Docker Desktop（Postgres/Redis/Airflow）
#
# 依赖分组安装（不依赖 editable install：项目脚本与测试已内置 sys.path 处理）；
# 若某组卡住，单独重跑该组命令并加 -v 观察输出。

$ErrorActionPreference = "Stop"

Write-Output "== 0/4 环境加固（本机用户配置 Temp 目录损坏，C 盘根目录非提权不可建）=="
# 临时目录策略：优先 D:\Temp（项目盘必然可写）；C:\Temp 在非提权终端无法创建，
# 若目录创建失败 tempfile 会静默回退到当前目录，导致 pip/Spark 行为异常
$tmpBase = if (Test-Path D:\) { "D:\Temp" } else { "$env:USERPROFILE\AppData\Local\Temp" }
New-Item -ItemType Directory -Force $tmpBase | Out-Null
if (-not (Test-Path $tmpBase)) { throw "无法创建临时目录 $tmpBase（请检查磁盘权限）" }
New-Item -ItemType Directory -Force "$tmpBase\pip-cache" | Out-Null
$env:TMP = $tmpBase
$env:TEMP = $tmpBase
$env:TMPDIR = $tmpBase
$env:PIP_NO_CACHE_DIR = "1"
$env:PIP_CACHE_DIR = "$tmpBase\pip-cache"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
# 持久化到用户环境变量（会话级设置在重开终端后会丢失）
[Environment]::SetEnvironmentVariable("TMP", $tmpBase, "User")
[Environment]::SetEnvironmentVariable("TEMP", $tmpBase, "User")
Write-Output "    TMP/TEMP -> $tmpBase（已持久化）；pip 缓存已禁用"

Write-Output "== 1/4 环境自检 =="
python --version
if ($LASTEXITCODE -ne 0) { throw "未找到 python，请先安装 Python 3.10-3.12 并加入 PATH" }
java -version 2>&1 | Select-Object -First 1
if ($LASTEXITCODE -ne 0) { Write-Warning "未找到 java：PySpark 无法运行，请安装 Temurin 17 后重试" }

Write-Output "== 2/4 创建虚拟环境（已存在则复用）=="
if (-not (Test-Path venv)) { python -m venv venv }
.\venv\Scripts\Activate.ps1
python -m pip install -U pip

Write-Output "== 3/4 分组安装依赖 =="
Write-Output "-- 3.1 测试三件套 --"
python -m pip install pytest pytest-cov httpx
Write-Output "-- 3.2 引擎核心库 --"
python -m pip install pyyaml sqlglot networkx pydantic pandas numpy
Write-Output "-- 3.3 PySpark（约 400MB，耗时最长）--"
python -m pip install "pyspark>=3.4,<4"
Write-Output "-- 3.4 服务层 --"
python -m pip install fastapi "uvicorn[standard]" redis "psycopg[binary]" streamlit
Write-Output "-- 3.5 AI 模块（可选，M3 联调时再装）--"
python -m pip install chromadb openai

Write-Output "== 4/4 生成小规模样本数据 =="
python foundation\data_gen\generate.py --scale small

Write-Output ""
Write-Output "== 完成 =="
Write-Output "下一步：python -m pytest tests\ -v   （116 项单测）"
Write-Output "        python scripts\poc_wap.py    （验证 Iceberg WAP 分支，首次拉取约 30MB jar）"
