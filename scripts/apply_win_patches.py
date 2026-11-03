"""本机 Windows 补丁（venv 重装后需重跑：python scripts/apply_win_patches.py）。

修复两类"路径含空格"导致的问题：
  1. spark-class2.cmd 对 %LAUNCHER_OUTPUT% 的操作未加引号 → 补引号 + for/f usebackq；
  2. --conf 值经 cmd %* 转发时空格被撕碎（实测 ClassNotFoundException: VS）
     → JVM 参数改由 spark-defaults.conf 承载（文件读取，天然免空格问题），
       且所有路径写入 8.3 短路径形式（无空格）。
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

JVM_OPTS_TPL = (
    "-Dhadoop.home.dir={hadoop} -Djava.library.path={hadoop}/bin "
    "-XX:TieredStopAtLevel=1 -XX:+UseSerialGC"
)

ALIAS = r"D:\dqp"


def _ensure_alias(repo: Path) -> None:
    """建无空格别名 D:\\dqp → 项目目录（junction，无需管理员）。

    本机 8.3 短路径被禁用，含空格的项目路径经 cmd %* 转发会撕裂 --conf 值
    （实测 ClassNotFoundException: VS），统一经别名访问项目路径规避。
    """
    if Path(ALIAS).is_dir():
        return
    import subprocess

    subprocess.run(
        ["cmd", "/c", "mklink", "/J", ALIAS, str(repo)],
        check=False,
        capture_output=True,
    )
    if not Path(ALIAS).is_dir():
        print(f"警告：无法创建 junction {ALIAS}（可能已存在或权限不足），将回退原路径")
    else:
        print(f"已创建无空格别名：{ALIAS} -> {repo}")


def _short(p) -> str:
    try:
        buf = ctypes.create_unicode_buffer(512)
        if ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, 512):
            return buf.value
    except Exception:  # noqa: BLE001
        pass
    return str(p)


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    _ensure_alias(repo)
    pyspark_dir = repo / "venv" / "Lib" / "site-packages" / "pyspark"
    if not pyspark_dir.is_dir():
        print(f"未找到 pyspark：{pyspark_dir}", file=sys.stderr)
        return 2

    # 路径统一走无空格别名（junction 不可用时回退原路径）
    base = Path(ALIAS) if Path(ALIAS).is_dir() else repo
    hadoop = str(base / ".hadoop").replace("\\", "/")

    # 1) spark-class2.cmd 引号补丁
    cmd_path = pyspark_dir / "bin" / "spark-class2.cmd"
    text = cmd_path.read_text(encoding="utf-8")
    patched = (
        text.replace("if exist %LAUNCHER_OUTPUT% goto :gen", 'if exist "%LAUNCHER_OUTPUT%" goto :gen')
        .replace("> %LAUNCHER_OUTPUT%", '> "%LAUNCHER_OUTPUT%"')
        .replace(
            'for /f "tokens=*" %%i in (%LAUNCHER_OUTPUT%) do (',
            'for /f "usebackq tokens=*" %%i in ("%LAUNCHER_OUTPUT%") do (',
        )
        .replace("del %LAUNCHER_OUTPUT%", 'del "%LAUNCHER_OUTPUT%"')
    )
    cmd_path.write_text(patched, encoding="utf-8")
    print(f"1) 已补丁 spark-class2.cmd：{cmd_path}")

    # 2) spark-defaults.conf（JVM 参数走文件，避免 --conf 空格撕裂）
    conf_dir = pyspark_dir / "conf"
    conf_dir.mkdir(exist_ok=True)
    opts = JVM_OPTS_TPL.format(hadoop=hadoop)
    (conf_dir / "spark-defaults.conf").write_text(
        f"spark.driver.extraJavaOptions\t{opts}\nspark.executor.extraJavaOptions\t{opts}\n",
        encoding="utf-8",
    )
    print(f"2) 已写入 spark-defaults.conf：{conf_dir / 'spark-defaults.conf'}")
    print(f"   JVM 参数：{opts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
