@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ============================================================
echo   bilibili-cheese-downloader  打包脚本 (Windows)
echo ============================================================
echo.

where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo [!] 未检测到 PyInstaller，正在安装依赖...
    echo.
    python --version 2>nul
    if errorlevel 1 (
        echo [!] 未检测到 Python，请先安装 Python 3.9-3.11：
        echo     https://www.python.org/downloads/
        echo     安装时务必勾选 "Add Python to PATH"
        pause
        exit /b 1
    )
    python -m pip install --upgrade pip
    python -m pip install tqdm aiohttp bilibili-api-python pyinstaller
    if errorlevel 1 (
        echo [!] 依赖安装失败，请检查网络或 Python 环境
        pause
        exit /b 1
    )
)

echo [*] 开始打包（首次约 1-3 分钟，请耐心等待）...
echo.

python build_exe.py
if errorlevel 1 (
    echo.
    echo [!] 打包失败，请将上方红色错误信息截图发给我
    pause
    exit /b 1
)

echo.
echo [+] 打包成功！
echo.
if exist "dist\bilibili-cheese-downloader.exe" (
    for %%I in ("dist\bilibili-cheese-downloader.exe") do echo     文件大小: %%~zI 字节
    echo     文件位置: %cd%\dist\bilibili-cheese-downloader.exe
) else (
    echo [!] 未找到输出文件，请检查上方日志
)
echo.
pause
