@echo off
set DL_BIN=C:\Users\RituMishra\dataloader_v66.0.0\bin
set DL_CONF="C:\Users\RituMishra\Downloads\Enquesta Integration Files\Enquesta Integration Files\SalesforceToEnquestaProcessFiles"

call "%DL_BIN%\process.bat" %DL_CONF% parcelInvoiceExtractProcess
if %ERRORLEVEL% NEQ 0 (
    echo Extraction failed with error %ERRORLEVEL%. Skipping mark-sent step.
    exit /b %ERRORLEVEL%
)

call "%DL_BIN%\process.bat" %DL_CONF% parcelInvoiceMarkSentProcess
if %ERRORLEVEL% NEQ 0 (
    echo Mark-sent update failed with error %ERRORLEVEL%.
    exit /b %ERRORLEVEL%
)

echo Both steps completed successfully.
