@echo off
cd /d "%~dp0"

echo ============================================
echo  FIDAL CdS Tool -- Build EXE
echo ============================================
echo.

:: Verifica Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERRORE: Python non trovato nel PATH.
    echo Installa Python 3.8+ da python.org e spunta
    echo "Add Python to PATH" durante l'installazione.
    echo.
    pause
    exit /b 1
)
echo Python trovato:
python --version
echo.

:: Installa dipendenze tramite modulo (evita problemi PATH di pip)
echo [1/3] Installazione dipendenze...
python -m pip install flask requests beautifulsoup4 pyinstaller
if errorlevel 1 (
    echo.
    echo ERRORE durante l'installazione delle dipendenze.
    pause
    exit /b 1
)

:: Pulizia build precedente
echo.
echo [2/3] Pulizia build precedente...
if exist dist\FIDAL_CDS_Tool.exe del /q dist\FIDAL_CDS_Tool.exe
if exist build rmdir /s /q build

:: Build tramite modulo (evita problemi PATH di pyinstaller)
echo.
echo [3/3] Build in corso (1-2 minuti)...
python -m PyInstaller --clean fidal_cds_tool.spec
if errorlevel 1 (
    echo.
    echo ERRORE durante la build. Controlla i messaggi sopra.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Build completata con successo!
echo  File: dist\FIDAL_CDS_Tool.exe
echo ============================================
for %%f in (dist\FIDAL_CDS_Tool.exe) do echo  Dimensione: %%~zf byte
echo.
pause
