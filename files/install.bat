@echo off
REM Memopad MCP Server Installation Script for Windows
REM Double-click to run

echo ========================================
echo Memopad MCP Server Installation
echo ========================================
echo.

REM Set installation directory
set INSTALL_DIR=F:\ANTI\memopad

REM Check if Python is installed
echo Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    echo Please install Python 3.10+ from https://www.python.org/
    echo.
    pause
    exit /b 1
)

python --version
echo [OK] Python found
echo.

REM Create installation directory
echo Creating installation directory...
if not exist "%INSTALL_DIR%" (
    mkdir "%INSTALL_DIR%"
    echo [OK] Created: %INSTALL_DIR%
) else (
    echo [OK] Directory exists: %INSTALL_DIR%
)
echo.

REM Copy files
echo Installing files...
set SCRIPT_DIR=%~dp0

if exist "%SCRIPT_DIR%memopad_server_fixed.py" (
    copy /Y "%SCRIPT_DIR%memopad_server_fixed.py" "%INSTALL_DIR%\server.py" >nul
    echo [OK] Copied server.py
) else (
    echo [WARNING] memopad_server_fixed.py not found
)

if exist "%SCRIPT_DIR%test_memopad.py" (
    copy /Y "%SCRIPT_DIR%test_memopad.py" "%INSTALL_DIR%\test_memopad.py" >nul
    echo [OK] Copied test_memopad.py
)

if exist "%SCRIPT_DIR%QUICKSTART.md" (
    copy /Y "%SCRIPT_DIR%QUICKSTART.md" "%INSTALL_DIR%\QUICKSTART.md" >nul
    echo [OK] Copied QUICKSTART.md
)

if exist "%SCRIPT_DIR%MEMOPAD_ANALYSIS.md" (
    copy /Y "%SCRIPT_DIR%MEMOPAD_ANALYSIS.md" "%INSTALL_DIR%\MEMOPAD_ANALYSIS.md" >nul
    echo [OK] Copied MEMOPAD_ANALYSIS.md
)

if exist "%SCRIPT_DIR%README.md" (
    copy /Y "%SCRIPT_DIR%README.md" "%INSTALL_DIR%\README.md" >nul
    echo [OK] Copied README.md
)

echo.

REM Create storage directory
set STORAGE_DIR=%USERPROFILE%\.memopad
echo Creating storage directory...
if not exist "%STORAGE_DIR%" (
    mkdir "%STORAGE_DIR%"
    echo [OK] Created: %STORAGE_DIR%
) else (
    echo [OK] Storage directory exists
)
echo.

REM Test installation
echo Testing installation...
if exist "%INSTALL_DIR%\server.py" (
    python -m py_compile "%INSTALL_DIR%\server.py" 2>nul
    if errorlevel 1 (
        echo [WARNING] Could not validate server file
    ) else (
        echo [OK] Server file validated
    )
) else (
    echo [ERROR] Server file not found!
)
echo.

REM Create config info
set CLAUDE_CONFIG=%APPDATA%\Claude\claude_desktop_config.json

echo ========================================
echo Installation Summary
echo ========================================
echo.
echo Installation directory: %INSTALL_DIR%
echo Storage directory: %STORAGE_DIR%
echo Claude config: %CLAUDE_CONFIG%
echo.

echo [OK] Installation complete!
echo.
echo ----------------------------------------
echo Next Steps:
echo ----------------------------------------
echo 1. Configure Claude Desktop
echo    Edit: %CLAUDE_CONFIG%
echo.
echo    Add this configuration:
echo    {
echo      "mcpServers": {
echo        "memopad": {
echo          "command": "python",
echo          "args": ["F:\\ANTI\\memopad\\server.py"]
echo        }
echo      }
echo    }
echo.
echo 2. Restart Claude Desktop
echo.
echo 3. Test by asking Claude:
echo    "Create a note titled 'Test' with content 'Hello World'"
echo.
echo ----------------------------------------
echo Optional: Run Tests
echo ----------------------------------------
echo cd %INSTALL_DIR%
echo python test_memopad.py
echo.
echo ----------------------------------------

REM Ask if user wants to open config file
echo.
set /p OPEN_CONFIG="Would you like to open the Claude config file now? (y/n): "
if /i "%OPEN_CONFIG%"=="y" (
    if exist "%CLAUDE_CONFIG%" (
        notepad "%CLAUDE_CONFIG%"
    ) else (
        echo Creating new config file...
        mkdir "%APPDATA%\Claude" 2>nul
        (
            echo {
            echo   "mcpServers": {
            echo     "memopad": {
            echo       "command": "python",
            echo       "args": ["F:\\ANTI\\memopad\\server.py"]
            echo     }
            echo   }
            echo }
        ) > "%CLAUDE_CONFIG%"
        notepad "%CLAUDE_CONFIG%"
    )
)

echo.
echo Installation script finished.
pause
