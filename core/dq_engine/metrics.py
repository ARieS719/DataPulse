# 兼容性 shim：实现已迁移为包 dq_engine/metrics/__init__.py
# （避免与 metrics/examples/ 数据目录产生同名遮蔽）
from dq_engine.metrics import *  # noqa: F401,F403
from dq_engine.metrics import __all__  # noqa: F401
