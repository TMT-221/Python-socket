$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'SocketRoom.lnk'
$target = Join-Path $project 'start_public.bat'
if (-not (Test-Path -LiteralPath $target)) {
    throw "Không tìm thấy start_public.bat"
}
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $project
$shortcut.WindowStyle = 7
$shortcut.Description = 'Khởi động SocketRoom và tạo liên kết chat khi đăng nhập Windows'
$shortcut.Save()
Write-Host "Đã bật tự khởi động sau khi đăng nhập Windows."
Write-Host "Link sẽ được ghi vào: $(Join-Path $project 'public_link.txt')"

