"""pytest 全局配置：把仓库根目录与 core/ 加入 sys.path。

未安装 editable 包（pip install -e .）时也能导入：
  foundation / eval / chaos / web（在仓库根目录）
  dq_engine（在 core/ 子目录）
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPO_ROOT, REPO_ROOT / "core"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
