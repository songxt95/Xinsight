@echo off
chcp 65001 >nul 2>&1

REM 默认绑定 IP，可通过参数覆盖: generate_cert.bat 192.168.1.69
set IP=%1
if "%IP%"=="" set IP=10.25.214.11

echo ========================================
echo   Xinsight HTTPS 自签证书生成器
echo   绑定 IP: %IP%
echo ========================================
echo.

REM 检查证书是否已存在
if exist "cert.pem" (
    if exist "key.pem" (
        echo [提示] cert.pem 和 key.pem 已存在。
        set /p overwrite=是否覆盖？(y/N):
        if /i not "%overwrite%"=="y" (
            echo 已取消。
            pause
            exit /b 0
        )
    )
)

REM 查找 openssl
set OPENSSL=
for %%p in (
    "C:\Program Files\Git\usr\bin\openssl.exe"
    "C:\Program Files\Git\mingw64\bin\openssl.exe"
    "C:\Program Files (x86)\Git\usr\bin\openssl.exe"
) do (
    if exist %%p set OPENSSL=%%~p
)

if "%OPENSSL%"=="" (
    where openssl >nul 2>&1
    if %errorlevel%==0 (
        set OPENSSL=openssl
    )
)

if "%OPENSSL%"=="" (
    echo [错误] 未找到 openssl，请确认已安装 Git for Windows。
    echo         或将 openssl.exe 所在目录加入 PATH。
    pause
    exit /b 1
)

echo [1/3] 生成私钥和证书...
%OPENSSL% req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 3650 -nodes -subj "/CN=%IP%" -addext "subjectAltName=IP:%IP%"

if %errorlevel% neq 0 (
    echo [错误] 证书生成失败。
    pause
    exit /b 1
)

echo.
echo [2/3] 证书生成成功！
echo   - cert.pem (证书)
echo   - key.pem  (私钥)
echo   - 有效期: 10 年
echo   - 绑定 IP: %IP%
echo.
echo [3/3] 老师首次访问 https://%IP%:6927 时：
echo   1. 浏览器会提示"您的连接不是私密连接"
echo   2. 点"高级" -> "继续前往 %IP%（不安全）"
echo   3. 之后不再提示，剪贴板功能自动可用
echo.
pause
