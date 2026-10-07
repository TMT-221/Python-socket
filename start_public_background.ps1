param([string]$ProjectPath = '')
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($ProjectPath)) { $ProjectPath = $PSScriptRoot }
. (Join-Path $PSScriptRoot 'socketroom_process.ps1')
$project = Get-SocketRoomProjectPath $ProjectPath
$mutex = New-SocketRoomMutex $project
$held = $false
$exitCode = 1
try {
    $held = Enter-SocketRoomMutex $mutex 0
    if (-not $held) {
        Write-Host 'Một lần khởi động hoặc dừng SocketRoom đang xử lý. Hãy đợi rồi mở lại.'
        $exitCode = 0
    } else {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $launcher = Get-SocketRoomLauncher $project
        $startedProcess = $null
        if ($launcher) {
            Write-Host 'SocketRoom đã chạy. Đang kiểm tra link hiện tại...'
        } else {
            if (-not (Test-Path -LiteralPath (Join-Path $project 'start_public.py'))) { throw 'Thiếu start_public.py. Hãy giải nén đầy đủ gói SocketRoom.' }
            Remove-SocketRoomMarkers $project
            $logDir = Join-Path $project 'logs'
            New-Item -ItemType Directory -Path $logDir -Force | Out-Null
            $pythonCommand = Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1
            $pythonPath = (& $pythonCommand.Source -c 'import sys; print(sys.executable)' 2>$null | Select-Object -Last 1)
            if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($pythonPath) -or -not (Test-Path -LiteralPath $pythonPath)) {
                throw 'Không tìm được Python. Cài Python và chọn Add Python to PATH.'
            }
            $env:PYTHONUNBUFFERED = '1'
            $env:PYTHONUTF8 = '1'
            $env:PYTHONIOENCODING = 'utf-8'
            $scriptArgument = '"' + (Join-Path $project 'start_public.py') + '"'
            $startedProcess = Start-Process -FilePath $pythonPath -ArgumentList $scriptArgument -WorkingDirectory $project -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'launcher.log') -RedirectStandardError (Join-Path $logDir 'launcher_error.log')
            Write-Host 'SocketRoom đang chạy nền. Đang tạo và kiểm tra link HTTPS...'
        }
        $deadline = [DateTime]::UtcNow.AddSeconds(300)
        $readyLink = $null
        while ([DateTime]::UtcNow -lt $deadline) {
            if ($startedProcess) {
                $startedProcess.Refresh()
                if ($startedProcess.HasExited) { throw 'SocketRoom khởi động không thành công. Xem logs\launcher_error.log và logs\launcher.log.' }
            } elseif (-not (Get-SocketRoomLauncher $project)) {
                throw 'Phiên SocketRoom đã dừng. Hãy mở start_public.bat để chạy lại.'
            }
            $link = Get-SocketRoomPublicLink $project
            if ($link -and (Test-SocketRoomHealth 'http://127.0.0.1:8080' 3) -and (Test-SocketRoomHealth $link 5)) {
                $readyLink = $link
                break
            }
            Start-Sleep -Seconds 2
        }
        if (-not $readyLink) {
            throw 'Link HTTPS chưa truy cập được. Xem logs\tunnel.log; kiểm tra Internet. Nếu cần chạy lại, dùng stop_public.bat rồi start_public.bat.'
        }
        Write-Host ''
        Write-Host ('LINK PHÒNG CHAT: ' + $readyLink) -ForegroundColor Green
        Write-Host ('Link được lưu tại: ' + (Join-Path $project 'public_link.txt'))
        Write-Host 'Sao chép link trên gửi cho mọi người. Có thể đóng cửa sổ này; SocketRoom tiếp tục chạy nền.'
        Write-Host 'Giữ máy bật và có Internet. Muốn dừng, chạy stop_public.bat.'
        $exitCode = 0
    }
} catch {
    Write-Host ('LỖI: ' + $_.Exception.Message) -ForegroundColor Red
} finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
exit $exitCode
