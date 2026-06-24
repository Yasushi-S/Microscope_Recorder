# 顕微鏡録画アプリのショートカットをデスクトップに作成し、タスクバーへのピン留めを試みる
$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$icon = Join-Path $root "icon.ico"
$script = Join-Path $root "microscope_recorder.py"

if (-not (Test-Path $pythonw)) {
    Write-Host "エラー: $pythonw が見つかりません。先に run.bat を一度実行して環境をセットアップしてください。"
    exit 1
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "顕微鏡録画システム.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "`"$script`""
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = "$icon,0"
$shortcut.Description = "顕微鏡録画システムを起動します"
$shortcut.Save()

Write-Host "デスクトップにショートカットを作成しました: $shortcutPath"

# タスクバーへのピン留めを試みる（Windowsのバージョンによっては自動ピン留めが
# 許可されておらず失敗することがある。失敗時は手動での操作を案内する）
try {
    $folder = $shell2 = (New-Object -ComObject Shell.Application).NameSpace($desktop)
    $item = $folder.ParseName((Split-Path $shortcutPath -Leaf))
    $pinVerb = $item.Verbs() | Where-Object { $_.Name -replace '&', '' -match 'タスク バーに固定|タスクバーにピン留め|Pin to tas?kbar' }
    if ($pinVerb) {
        $pinVerb.DoIt()
        Start-Sleep -Milliseconds 500
        Write-Host "タスクバーへのピン留めに成功しました。"
    } else {
        throw "ピン留め用の操作（verb）が見つかりませんでした。"
    }
} catch {
    Write-Host ""
    Write-Host "タスクバーへの自動ピン留めはできませんでした。"
    Write-Host "Windows 11ではデスクトップのショートカットを右クリックしても「タスクバーにピン留め」"
    Write-Host "という項目が表示されません。お手数ですが、以下の手順でピン留めしてください（1回だけで完了します）："
    Write-Host "  1. デスクトップの「顕微鏡録画システム」をダブルクリックしてアプリを起動する"
    Write-Host "  2. タスクバーに表示されたアイコンを右クリック"
    Write-Host "  3. 「タスクバーにピン留めする」を選択"
}
