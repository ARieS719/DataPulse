# DataPulse 环境自检脚本（在 VS Code 终端执行）：
#   powershell -ExecutionPolicy Bypass -File scripts\check_env.ps1
# 把输出整段粘贴回来，用于确认后续安装路径。

Write-Output "=== DataPulse 环境检查 ==="
Write-Output ("时间: " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))

function TryCmd([string]$name, [string]$exe, [string[]]$argsArr) {
    Write-Output ("--- " + $name)
    try {
        & $exe @argsArr 2>&1 | ForEach-Object { "$_" }
    } catch {
        Write-Output "NOT FOUND / FAILED"
    }
}

TryCmd "python" "python" @("--version")
TryCmd "py launcher" "py" @("--version")
TryCmd "java" "java" @("-version")
TryCmd "docker CLI" "docker" @("--version")
TryCmd "docker daemon" "docker" @("info")
TryCmd "git" "git" @("--version")

Write-Output ("--- RAM_GB: " + [int]((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB))
Write-Output ("--- 逻辑核心数: " + (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors)
Write-Output "=== 检查结束 ==="
