"""共享 SparkSession 工厂：local 模式 + Iceberg（Hadoop Catalog, 本地 warehouse 目录）。

首次运行会通过 Maven 拉取 iceberg-spark-runtime jar（约 30MB，需联网）。

版本依据：Spark 3.4+ / Iceberg 1.4+ 才稳定支持分支 DDL（CREATE BRANCH / fast_forward）
与 WAP 写入（spark.wap.branch 会话配置），因此版本下限锁定。
"""

from __future__ import annotations

import ctypes
from pathlib import Path

from pyspark.sql import SparkSession

ICEBERG_VERSION = "1.5.2"
SPARK_MAJOR = "3.5"


def _short_path(p: str) -> str:
    """Windows 8.3 短路径（部分卷禁用 8.3 命名时回退原路径）。"""
    try:
        buf = ctypes.create_unicode_buffer(512)
        if ctypes.windll.kernel32.GetShortPathNameW(p, buf, 512):
            return buf.value
    except Exception:  # noqa: BLE001 非 Windows 环境回退原路径
        pass
    return p


def _alias_root(repo_root: Path) -> Path:
    """无空格别名（apply_win_patches.py 创建的 D:\\dqp junction），
    规避含空格路径经 cmd %* 转发撕裂 --conf 值的问题。"""
    alias = Path("D:/dqp")
    return alias if alias.is_dir() else repo_root


def build_spark(
    app_name: str = "datapulse",
    warehouse: str | Path = "warehouse",
    driver_memory: str = "3g",
) -> SparkSession:
    repo_root = Path(__file__).resolve().parents[2]
    root = _alias_root(repo_root)
    warehouse = _short_path(str(Path(warehouse).resolve()))
    # Ivy 缓存必须放在项目内：本机安全策略只允许项目目录树的写入，
    # 默认的 ~/.ivy2（用户主目录）会导致 spark.jars.packages 下载 Iceberg jar 时 FileNotFoundException
    ivy_dir = _short_path(str(root / ".ivy2"))
    # 注意：JVM 参数（hadoop.home.dir / java.library.path / GC / JIT 加固）不走 --conf
    # （含空格的 --conf 值经 cmd %* 转发会被撕碎，实测 ClassNotFoundException: VS），
    # 统一由 scripts/apply_win_patches.py 写入 spark-defaults.conf 承载。
    return (
        SparkSession.builder.appName(app_name)
        .master("local[2]")
        .config("spark.driver.memory", driver_memory)
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.jars.ivy", ivy_dir)
        .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
        .config("spark.sql.catalog.local", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.local.type", "hadoop")
        .config("spark.sql.catalog.local.warehouse", warehouse)
        # 本机稳定性加固：Windows 上 snappy/zstd 原生库曾导致 JVM EXCEPTION_ACCESS_VIOLATION
        # （hs_err 实证，snappyjava.dll/libzstd-jni.dll 加载后崩溃），统一改用纯 Java 实现的 LZ4
        .config("spark.sql.catalog.local.write.parquet.compression-codec", "lz4")
        .config("spark.sql.parquet.compression.codec", "lz4")
        .config("spark.io.compression.codec", "lz4")
        # WAP（Write-Audit-Publish）注意：spark.wap.branch 会话参数生效的前提是
        # 目标表带 TBLPROPERTIES('write.wap.enabled'='true')（表级属性，非 catalog 属性，
        # 依据 Iceberg 1.5.2 SparkWriteConf.wapEnabled 源码）——建表语句见 etl_ods.py
        .config("spark.jars.packages", f"org.apache.iceberg:iceberg-spark-runtime-{SPARK_MAJOR}_2.12:{ICEBERG_VERSION}")
        .getOrCreate()
    )
