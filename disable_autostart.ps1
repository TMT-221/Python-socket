$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'SocketRoom.lnk'
if (Test-Path -LiteralPath $shortcutPath) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    if ($shortcut.TargetPath -ne (Join-Path $project 'start_public.bat')) {
        throw 'SocketRoom.lnk đang trỏ tới dự án khác; không xóa.'
    }
    Remove-Item -LiteralPath $shortcutPath
}
Write-Host 'Đã tắt tự khởi động SocketRoom.'

