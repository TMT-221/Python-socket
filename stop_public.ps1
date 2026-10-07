$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'socketroom_process.ps1')
$project = Get-SocketRoomProjectPath $PSScriptRoot
$mutex = New-SocketRoomMutex $project
$held = $false
$exitCode = 1
try {
    $held = Enter-SocketRoomMutex $mutex 0
    if (-not $held) {
        Write-Host 'SocketRoom đang khởi động hoặc dừng. Hãy đợi thao tác đó xong rồi chạy lại.'
        $exitCode = 1
    } else {
        $launcher = Get-SocketRoomLauncher $project
        if ($launcher) {
            $verified = Get-SocketRoomLauncher $project
            if (-not $verified -or $verified.Id -ne $launcher.Id -or $verified.CreatedUtc -ne $launcher.CreatedUtc) {
                throw 'Phiên SocketRoom đã thay đổi. Chạy stop_public.bat lại.'
            }
            & taskkill.exe /PID $verified.Id /T /F | Out-Null
            if ($LASTEXITCODE -ne 0) {
                if (Get-SocketRoomLauncher $project) { throw 'Không dừng được phiên SocketRoom. Hãy kiểm tra quyền Windows.' }
            }
            $deadline = [DateTime]::UtcNow.AddSeconds(10)
            while ((Get-SocketRoomLauncher $project) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 250 }
            if (Get-SocketRoomLauncher $project) { throw 'Phiên SocketRoom chưa dừng. Chạy stop_public.bat lại.' }
            Remove-SocketRoomMarkers $project
            Write-Host 'Đã dừng SocketRoom và link công khai.'
        } else {
            Remove-SocketRoomMarkers $project
            Write-Host 'Không có phiên SocketRoom đang chạy từ thư mục này. Đã xóa link cũ.'
        }
        $exitCode = 0
    }
} catch {
    Write-Host ('LỖI: ' + $_.Exception.Message) -ForegroundColor Red
} finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
exit $exitCode
