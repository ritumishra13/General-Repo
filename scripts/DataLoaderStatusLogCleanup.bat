@echo off
setlocal enabledelayedexpansion

:: Directory containing Data Loader status logs (success/error files)
set "STATUS_DIR=E:\CLI Services\CLI Services\LIBillBatchIntegrationSTOEProcessService\salesforce\Dataloader_v67.0.0\COWCLI\status"

if not exist "%STATUS_DIR%" (
    echo ERROR: Status directory not found or not reachable: %STATUS_DIR%
    exit /b 1
)

echo Cleaning up Success/Error log files older than 3 months in: %STATUS_DIR%

forfiles /p "%STATUS_DIR%" /s /m *success* /d -90 /c "cmd /c if @isdir==FALSE del /q @path"
if %errorlevel% equ 1 echo No matching success log files older than 3 months were found.

forfiles /p "%STATUS_DIR%" /s /m *error* /d -90 /c "cmd /c if @isdir==FALSE del /q @path"
if %errorlevel% equ 1 echo No matching error log files older than 3 months were found.

echo Cleanup completed.

endlocal
