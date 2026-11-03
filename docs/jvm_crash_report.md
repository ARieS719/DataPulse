# 求助：Windows 11 上 JVM 在大数据量「读 Parquet → 写 Iceberg」时确定性崩溃

> ✅ **【已解决 · 2026-11-04】** 经外部建议 + 二分实验锁定根因：
> **Iceberg 的向量化 Parquet 读取器（Arrow 堆外/Unsafe 内存路径）在本机触发 JVM 内存破坏。**
> 修复：所有 Iceberg 表建表时加表属性 `'read.parquet.vectorization.enabled'='false'`
>（读时也可加 `.option("vectorization-enabled", "false")`）。
> 注意：`spark.sql.parquet.enableVectorizedReader=false` 对 Iceberg 表**无效**（Iceberg 有独立读取器）。
> 二分证据：只关向量化读、其余配置全部还原 → 100 万行 Parquet→Iceberg 写入一次通过（16.6s），
> medium 四层全链路 + 对账 3/3 全部跑通。排查全过程见 docs/troubleshooting.md 坑 #6。

> 这是发给外部（其他 AI / 同学 / 社区）的完整故障求助文档，可独立阅读。
> 项目背景：PySpark + Apache Iceberg 的湖仓数据质量平台（本地单机 demo）。
> 我们已穷尽常见的软件层面缓解措施，怀疑是机器级问题（内存硬件 / 虚拟化 / 安全软件注入），
> 欢迎任何新线索。

---

## 1. 问题概述

在一台 Windows 11 笔记本上，Spark 任务执行到 **「读取一张约 100 万行的 Iceberg 表（Parquet 格式）→ 转换 → 写回另一张 Iceberg 表」** 时，JVM 进程必定崩溃（`EXCEPTION_ACCESS_VIOLATION (0xc0000005)`）。而以下操作全部正常：

- 同一批 100 万行数据 **CSV → Iceberg 写入**（成功多次，约 28 秒）；
- 同一批 100 万行数据 **CSV → 普通 Parquet 写入**（成功多次）；
- 100 万行 Parquet **读取 + count**（成功）；
- 1 万行规模下**全流程完全稳定**（四层数仓 + WAP 分支 + 混沌实验全部通过）。

即崩溃单元格是：**Parquet(Iceberg) 来源的 DataFrame → Iceberg 写入，≥ 50 万行时 100% 崩溃**。

## 2. 环境

| 项 | 值 |
| --- | --- |
| CPU | Intel i5-12500H（Alder Lake 混合架构：4 P 核 + 8 E 核，16 线程） |
| 内存 | 16 GB（可见 15.7 GB；崩溃时空闲 > 6 GB，非内存耗尽） |
| OS | Windows 11 64-bit，Build 22621.2506（22H2） |
| Python | 3.11.9 |
| PySpark | 3.5.9（local[2] 单机模式，driver 3g） |
| Iceberg | 1.5.2（iceberg-spark-runtime-3.5_2.12，Hadoop catalog，本地 warehouse） |
| Java | Temurin 17.0.20.1（默认）、21.0.13、8.0.504 —— 三个版本都测过，都崩 |
| 其他 | winutils.exe + hadoop.dll 已配置（hadoop.home.dir / java.library.path）；压缩统一 LZ4 |

## 3. 崩溃特征（hs_err 日志 / Windows 事件日志实证）

- JVM 报告：`# A fatal error has been detected ... EXCEPTION_ACCESS_VIOLATION (0xc0000005)`，然后 py4j 报 `ConnectionResetError` / `[JAVA_GATEWAY_EXITED]`（JVM 死了）。
- 崩溃地址不固定：ntdll.dll（WER 记录 offset `0x10c7a9`）、jvm.dll（用户侧两次崩溃 offset 完全相同：`jvm.dll+0x7a89ab`；其他次：`+0x6e3b3b`、`+0x833d47`）、JDK 8 下又是另一个地址。
- 崩溃线程随机：`Executor task launch worker`、`VM Thread`（GC 线程）、`C1 CompilerThread`、`kill-executor-thread` 都当过崩溃线程。
- 部分 hs_err 中 `siginfo: reading address 0x00000000000615c7`（极小的近零地址）。
- 崩溃前后 JVM 内部异常日志多次出现 `NoSuchMethodError: InternalRow.<clinit>`。
- 同一故障在**用户自己的终端**和**AI 助手的执行通道**下都能复现 → 与调用方无关，机器级。

## 4. 复现步骤

数据：`fact_order.csv` 100 万行（约 81 MB），字段含 DECIMAL(12,2) 金额、TIMESTAMP 时间等。

```powershell
# 先落 ODS 层（此步稳定成功）
python scripts\run_pipeline.py --scale medium --stages ods
```

最小复现（无需项目其他代码）：

```python
from pyspark.sql import SparkSession

spark = (
    SparkSession.builder.appName("repro")
    .master("local[2]")
    .config("spark.driver.memory", "3g")
    .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
    .config("spark.sql.catalog.local", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.local.type", "hadoop")
    .config("spark.sql.catalog.local.warehouse", "<仓库路径>")
    .config("spark.jars.packages", "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.2")
    .getOrCreate()
)

n = spark.table("local.ods.fact_order").count()
print("READ OK", n)                      # ✅ 输出 1000000，永远成功

spark.sql("DROP TABLE IF EXISTS local.scratch.t")
spark.sql(
    "CREATE TABLE local.scratch.t (order_id STRING, user_id STRING, order_time TIMESTAMP, "
    "channel STRING, category STRING, amount DECIMAL(12,2), coupon_discount DECIMAL(12,2), "
    "status STRING) USING iceberg"
)
spark.table("local.ods.fact_order").writeTo("local.scratch.t").append()
print("WRITE OK")                        # ❌ 到这行之前 JVM 必崩
```

补充二分结果：

- `limit(500000)` 也崩；
- `.repartition(8)`（拆 8 个小文件）也崩；
- 目标表加不加分区（`PARTITIONED BY days(order_time)`）都崩；
- 去掉转换、原样 `writeTo` 也崩（排除转换逻辑）；
- 仅 `count()` 不写：永远成功。

## 5. 已尝试且无效的缓解措施（全部实测）

| # | 措施 | 结果 |
| --- | --- | --- |
| 1 | JDK 换 21.0.13、8.0.504 | 仍崩 |
| 2 | GC 换 SerialGC（替代 G1） | 仍崩 |
| 3 | `-XX:TieredStopAtLevel=1`（只留 C1 编译器） | 仍崩 |
| 4 | `-Xint`（完全关闭 JIT 纯解释执行） | 仍崩 |
| 5 | `-XX:+UnlockDiagnosticVMOptions -XX:-UseCRC32Intrinsics -XX:-UseCRC32CIntrinsics` | 仍崩 |
| 6 | `-XX:UseAVX=0`、禁用 SHA/SHA256 系列 intrinsic | 仍崩 |
| 7 | 进程亲和性钉在 P 核（`ProcessorAffinity=0xFF`） | 仍崩 |
| 8 | 压缩全部换 LZ4（shuffle / parquet / Iceberg 写） | 仍崩 |
| 9 | `repartition(8)` 拆小文件、`limit(500000)` | 仍崩 |
| 10 | Windows Defender 排除（java.exe、项目目录、JDK 目录） | 仍崩 |

## 6. 机器上的其他异常现象（可能相关）

这台机器整体行为异常，疑似云桌面/管控环境：

1. 用户 profile 的 Temp 目录损坏 → pip 安装会无响应挂起（已通过重定向 temp 解决）；
2. python 派生的 cmd/java 子进程**无法写项目目录树之外**（如 D:\Temp），PowerShell 直接派生却可以 → 有进程链级安全策略；
3. 某个 AI 助手工具注入进程时 PowerShell 曾以 0xC0000142 崩溃；
4. Windows 内存诊断（mdsched）尚未运行（需要重启，尚未执行）。

## 7. 我们怀疑的方向（求验证/反驳）

1. **内存硬件故障**（bit flip）：崩溃点随机、线程随机、跨 JVM 版本，符合坏内存特征——但为什么总精确命中"Parquet→Iceberg 写"这个操作而其他操作稳定？这一点不太像坏内存。
2. **Alder Lake P/E 混合核 + Win11 线程调度**：已知 12 代酷睿 + 早期 JDK 有一批线程迁移相关 bug；但钉 P 核无效，且 JDK 21 仍崩。
3. **云桌面管控/EDR 注入**：机器有强安全策略（见第 6 节），可能注入 DLL 破坏 JVM 内存；但 hs_err 的已加载 DLL 列表未发现第三方 DLL（也可能是内核级 hook 不显示）。
4. **Parquet 向量化读路径 + 本机 CPU/虚拟化组合的特定 bug**：崩溃总发生在"Parquet 读出来的 DataFrame 再写回"的场景。

## 8. 求助的具体问题

1. 有人见过 Temurin 17.0.20.1 `jvm.dll+0x7a89ab` 或 ntdll `0x10c7a9` 的崩溃吗？
2. 为什么「100 万行 CSV → Iceberg 写」稳定成功，而「100 万行 Parquet → Iceberg 写」必崩（数据、行数、目标表结构相同）？两者在 Spark 内部走的写路径差异可能是什么？
3. `spark.sql.parquet.enableVectorizedReader=false` 是否值得优先测试（还没测）？如果关掉向量化读就能过，能否据此定位根因？
4. 在不动硬件的前提下，还有哪些值得做的诊断（如 WER LocalDumps 抓 minidump + WinDbg、Driver Verifier、perfmon）？
5. 有没有可能是 Windows 11 22H2 某个更新 + 该硬件组合的已知问题？

## 9. 补充信息

- 项目路径含空格（`D:\Microsoft VS Code\lakehouse-dq-engine`），但相关的 cmd 引号问题已全部修复（spark-class2.cmd 补丁、无空格 junction 别名、JVM 参数走 spark-defaults.conf），**已排除路径空格因素**；
- 崩溃前无 OOM 迹象（heap 3g、空闲内存 6GB+）；
- 复现率 100%（该规模下每次必崩），可提供 hs_err 日志全文、完整数据生成脚本与复现脚本。

求各位 AI / 同学帮忙看看，任何线索都欢迎。谢谢！
