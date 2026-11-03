# 本机环境排查手册（Windows 11 笔记本实录）

> 记录本项目在本机（i5-12500H / 16GB / RTX 3050 6GB / Win11 22H2）从零搭建到跑通全链路踩过的所有坑。
> 每个坑都是真实排查过程，是面试讲述"工程问题定位能力"的一手素材。

## 0. 环境全景

| 组件 | 版本/位置 |
| --- | --- |
| Python | 3.11.9（D:\Python3.11），项目 venv：`venv/` |
| Java | Temurin 17.0.20.1（默认）/ 21.0.13 / 8.0.504（后两者为排查时安装） |
| Spark | PySpark 3.5.9（local[2] 单机模式） |
| Iceberg | 1.5.2（iceberg-spark-runtime-3.5_2.12，Hadoop catalog） |
| winutils/hadoop.dll | `.hadoop/bin/`（gitee 镜像下载） |
| 项目路径 | `D:\Microsoft VS Code\lakehouse-dq-engine`（含空格，见坑 #3） |
| 无空格别名 | `D:\dqp` → junction 指向项目（见坑 #4） |

## 坑 #1：pip 安装挂起（无任何输出）

- **现象**：`pip install` 输入命令后黑屏无响应（不是慢，是死锁）。
- **根因**：用户 profile 的 Temp 目录损坏（`C:\Users\Administrator\AppData\Local\Temp` 访问拒绝）+ pip.exe shim + 杀软扫描叠加。
- **修复**：`python -m pip install ... --no-cache-dir` + TMP/TEMP 重定向到项目内 `.tmp` 目录（持久化为用户级环境变量）。

## 坑 #2：安全策略：python 派生的子进程只能写项目目录树内

- **现象**：python → cmd → java 的进程链写 `D:\Temp`、`D:\sparktmp`、`~/.ivy2` 全部 Access denied；PowerShell 直接派生却可以。
- **结论**：本机有进程链级文件安全策略（云桌面管控特征），**所有临时目录、缓存目录必须落在项目目录树内**。
- **落地**：TMP/TEMP/TMPDIR 指向 `<项目>\.tmp`；Ivy 缓存 `.ivy2` 放项目内（`spark.jars.ivy`）。

## 坑 #3：项目路径含空格导致 spark-class2.cmd 静默失败

- **现象**：路径 `D:\Microsoft VS Code\lakehouse-dq-engine` 下 Spark 启动报 `'VS' is not recognized` / 找不到 launcher output 文件。
- **根因**：PySpark 的 `spark-class2.cmd` 第 71/74/75/78 行对 `%LAUNCHER_OUTPUT%`（位于 %temp%，temp 路径含空格）**未加引号**，cmd 按空格拆词执行失败。
- **修复（两条都要）**：
  1. 补丁 spark-class2.cmd：`if exist "%LAUNCHER_OUTPUT%"`、`> "%LAUNCHER_OUTPUT%"`、`for /f "usebackq tokens=*" %%i in ("%LAUNCHER_OUTPUT%") do (`、`del "%LAUNCHER_OUTPUT%"`。
  2. 注意 `for /f` 对引号内内容默认按**字符串**解析，必须加 `usebackq` 才按**文件**解析（踩过这个坑）。
- **自动化**：`python scripts/apply_win_patches.py` 一键打补丁（venv 重装后需重跑）。

## 坑 #4：--conf 值含空格被 cmd %* 撕碎 → ClassNotFoundException: VS

- **现象**：即使脚本补丁完成，`--conf spark.jars.ivy=D:\Microsoft VS Code\...` 等含空格的值经 `cmd /c ""script" %*"` 转发时被拆词，SparkSubmit 收到残缺参数报 `ClassNotFoundException: VS`。
- **尝试过的失效方案**：8.3 短路径（本机 D 卷禁用了 8.3 命名生成，GetShortPathNameW 无效）。
- **最终方案（三层）**：
  1. **无空格 junction 别名**：`New-Item -ItemType Junction -Path D:\dqp -Target "<项目>"`，所有配置路径写 `D:/dqp/...`；
  2. **JVM 参数走 spark-defaults.conf**（`venv/Lib/site-packages/pyspark/conf/`），文件解析天然免疫空格，不再走 --conf；
  3. `apply_win_patches.py` 统一生成。

## 坑 #5：HADOOP_HOME / winutils / hadoop.dll

- `FileNotFoundException: HADOOP_HOME and hadoop.home.dir unset` → 下载 `winutils.exe` 到 `.hadoop/bin/`，JVM 注入 `-Dhadoop.home.dir`；
- `NativeIO.access0 UnsatisfiedLinkError`（普通 Parquet 写路径）→ 补 `hadoop.dll` 到同目录 + `-Djava.library.path`；
- `hadoop.home.dir D:lakehouse... 不是绝对路径` → extraJavaOptions 经 cmd 转发时**反斜杠被吞**，一律写正斜杠。

## 坑 #6（最大的一坑）：JVM EXCEPTION_ACCESS_VIOLATION 崩溃

**现象**：medium 档（≥50 万行）「读 Iceberg 表 → 写回 Iceberg 表」时 JVM 必崩（`0xc0000005`），崩溃线程/地址随机（executor task / VM Thread / C1 编译线程都崩过）；而 CSV→Iceberg 写 100 万行、Parquet 读、1 万行全流程全部稳定。

**排查过程**（全部实测无效）：
JDK 8/17/21、G1/SerialGC、`-Xint`、`TieredStopAtLevel=1`、关 CRC/SHA/AVX intrinsic、钉 P 核（ProcessorAffinity=0xFF）、全链路 LZ4、repartition 拆小文件、Defender 排除——均无效。

**根因**（外部建议 + 二分实验锁定）：
**Iceberg 的向量化 Parquet 读取器**（Arrow 堆外/Unsafe 内存路径）在本机触发内存破坏。二分实验：只加
`'read.parquet.vectorization.enabled'='false'`（表属性）+ 读选项 `vectorization-enabled=false`，
其余全部还原标准配置 → 100 万行写入一次通过。

**修复**：所有建表语句 TBLPROPERTIES 增加 `'read.parquet.vectorization.enabled'='false'`（见 foundation/etl/*.py）。
注意 `spark.sql.parquet.enableVectorizedReader=false` **对 Iceberg 表无效**（Iceberg 有独立读取器）。

**修复后 medium 全链路实测**：
gen 30.2s → ods 27.6s（100 万订单）→ dwd 16.6s → dws 7.7s → ads 2.1s，对账 3/3 PASS。

> 详细求助文档与完整二分过程见 docs/jvm_crash_report.md。

## 坑 #7：WAP 写入不进审计分支 / fast_forward 报"非祖先"

- `spark.wap.branch` 会话参数被静默忽略 → WAP 需要**表级**属性 `write.wap.enabled=true`（非 catalog 属性，依据 Iceberg 1.5.2 SparkWriteConf 源码）。
- `CALL local.system.fast_forward('t','audit','main')` 报 `Cannot fast-forward: audit is not an ancestor of main` → 查 1.5.2 ManageSnapshots javadoc：**fastForwardBranch(from, to) 语义是把 from 引用快进到 to 的快照**，发布审计分支应调 `fast_forward('t','main','audit')`（main 追到 audit 的 tip）。

## 日常运行环境变量（VS Code 终端）

```powershell
# 用户级已持久化：TMP/TEMP = D:\Microsoft VS Code\lakehouse-dq-engine\.tmp
# 每次会话额外设置：
$env:JAVA_HOME="C:\Program Files\Eclipse Adoptium\jdk-17.0.20.101-hotspot"
$env:Path="$env:JAVA_HOME\bin;"+$env:Path
# 钉 P 核（本机稳定性习惯，可选）：
# (Get-Process -Id $PID).ProcessorAffinity = 255
```
