@echo off
echo ============================================
echo  FIDAL CdS Tool -- Build EXE
echo ============================================
echo.

:: Verifica Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERRORE: Python non trovato. Installa Python 3.8+ e riprova.
    pause & exit /b 1
)

:: Installa/aggiorna dipendenze
echo [1/3] Installazione dipendenze...
pip install flask requests beautifulsoup4 pyinstaller --quiet
if errorlevel 1 (
    echo ERRORE durante l'installazione delle dipendenze.
    pause & exit /b 1
)

:: Pulizia build precedente
echo [2/3] Pulizia build precedente...
if exist dist\FIDAL_CDS_Tool.exe del /q dist\FIDAL_CDS_Tool.exe
if exist build rmdir /s /q build

:: Build
echo [3/3] Build in corso (puo' richiedere 1-2 minuti)...
pyinstaller --clean fidal_cds_tool.spec
if errorlevel 1 (
    echo ERRORE durante la build.
    pause & exit /b 1
)

echo.
echo ============================================
echo  Build completata con successo!
echo  File: dist\FIDAL_CDS_Tool.exe
echo  Dimensione:
dir dist\FIDAL_CDS_Tool.exe | find "FIDAL"
echo ============================================
echo.
echo Puoi distribuire il file dist\FIDAL_CDS_Tool.exe
echo direttamente -- nessuna installazione richiesta.
pause
