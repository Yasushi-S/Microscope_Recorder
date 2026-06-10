# 顕微鏡録画アプリを Windows ログオン時に自動起動するタスクを登録する
$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $pythonw)) {
    Write-Host "エラー: $pythonw が見つかりません。先に run.bat を一度実行して環境をセットアップしてください。"
    exit 1
}

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "microscope_recorder.py" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

Register-ScheduledTask -TaskName "MicroscopeRecorder" -Action $action -Trigger $trigger `
    -Description "顕微鏡録画アプリをログオン時に自動起動" -Force | Out-Null

Write-Host "登録完了: 次回ログオン時からアプリが自動起動します。"
Write-Host "解除する場合は次のコマンドを実行してください:"
Write-Host "  Unregister-ScheduledTask -TaskName 'MicroscopeRecorder' -Confirm:`$false"
