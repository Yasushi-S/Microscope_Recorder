@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Pythonが見つかりません。Pythonをインストールしてからもう一度実行してください。
    timeout /t 10
    exit /b 1
)

if not exist .venv (
    echo 初回起動: 仮想環境を作成しています...
    python -m venv .venv
    if errorlevel 1 (
        echo 仮想環境の作成に失敗しました。
        timeout /t 10
        exit /b 1
    )

    call .venv\Scripts\activate.bat

    echo 必要なパッケージをインストールしています...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo パッケージのインストールに失敗しました。
        timeout /t 10
        exit /b 1
    )
) else (
    call .venv\Scripts\activate.bat
)

echo 顕微鏡録画システムを起動します...
python microscope_recorder.py
if errorlevel 1 (
    echo エラーが発生しました。
    timeout /t 10
)
