@echo off
chcp 65001 >nul
title B860AV1.1-T UBI flash (r79 image, 1GiB rootfs)
cd /d "%~dp0"

echo ============================================================
echo    B860AV1.1-T  UBI 灌入（r79 镜像 / rootfs 1GiB）
echo ============================================================
echo.
echo  前置条件：
echo    1. bootloader 已刷好（开机横幅 diag-r80 或 diag-r79）
echo    2. COM14 串口线已连，【关闭所有其它串口终端】
echo    3. 网线已连，本机 IP = 192.0.2.10
echo    4. tftp\ 目录内为 r79 分段（sha256 前缀 6221b5e0）
echo.

net session >nul 2>&1
if errorlevel 1 (
    echo [错误] 请右键本 bat - 以管理员身份运行（TFTP 69 端口需要）
    pause
    goto END
)

if not exist "%~dp0flash_ubi.ps1" (
    echo [错误] 找不到 %~dp0flash_ubi.ps1
    pause
    goto END
)

echo [1/3] 启动 TFTP 服务器（服务目录 %~dp0tftp）...
start "B860TFTP" /min cmd /c "python "%~dp0tftp_server.py""
timeout /t 2 >nul

echo [2/3] 经 COM14 灌入 UBI（擦除 + 4 段传输写入，约 10-20 分钟）...
echo       进度实时显示：擦除%% / 传输%%（每5%%一行）/ 写入心跳（每10秒）
echo       【期间不要碰串口终端！】
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0flash_ubi.ps1"
set RC=%ERRORLEVEL%

echo.
echo [3/3] 停止 TFTP 服务器...
taskkill /FI "WINDOWTITLE eq B860TFTP*" /F >nul 2>&1

echo.
if "%RC%"=="0" (
    echo ============ 灌入成功 ============
    echo 下一步：串口输入  reset  启动 Lakka
    echo 首次启动 userdata 自动格式化，然后恢复 03-restore\storage-backup.tgz
) else (
    echo ============ 失败 rc=%RC% ============
    echo 排查：COM14 是否被占用？TFTP 窗口有无报错？盒子网线是否连接？
    echo 可直接重新运行本 bat（脚本第一步是全量擦除，重跑安全）。
)
pause
:END
