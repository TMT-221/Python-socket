# Shared process checks for the SocketRoom Windows launchers.
function Get-SocketRoomProjectPath {
    param([Parameter(Mandatory=$true)][string]$Project)
    return [IO.Path]::GetFullPath($Project).TrimEnd('\', '/')
}

function New-SocketRoomMutex {
    param([Parameter(Mandatory=$true)][string]$Project)
    $normalized = (Get-SocketRoomProjectPath $Project).ToLowerInvariant()
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($normalized))).Replace('-', '') }
    finally { $sha.Dispose() }
    return New-Object Threading.Mutex($false, ('Local\SocketRoom-' + $hash))
}

function Enter-SocketRoomMutex {
    param([Parameter(Mandatory=$true)]$Mutex, [int]$TimeoutMs = 0)
    try { return $Mutex.WaitOne($TimeoutMs) }
    catch [Threading.AbandonedMutexException] { return $true }
}

if (-not ('SocketRoomWindowsArguments' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class SocketRoomWindowsArguments {
    [DllImport("shell32.dll", SetLastError=true)]
    static extern IntPtr CommandLineToArgvW([MarshalAs(UnmanagedType.LPWStr)] string commandLine, out int argc);
    [DllImport("kernel32.dll")]
    static extern IntPtr LocalFree(IntPtr memory);
    public static string[] Split(string commandLine) {
        int count;
        IntPtr memory = CommandLineToArgvW(commandLine, out count);
        if (memory == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
        try {
            string[] result = new string[count];
            for (int i = 0; i < count; i++)
                result[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(memory, i * IntPtr.Size));
            return result;
        } finally { LocalFree(memory); }
    }
}
'@ -ErrorAction Stop
}

function Get-SocketRoomLauncher {
    param([Parameter(Mandatory=$true)][string]$Project)
    $projectPath = Get-SocketRoomProjectPath $Project
    $expectedScript = Join-Path $projectPath 'start_public.py'
    $statePath = Join-Path $projectPath 'launcher_state.json'
    $pidPath = Join-Path $projectPath 'launcher.pid'
    $stateValidated = $false
    try {
        if (Test-Path -LiteralPath $statePath) {
            $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
            $launcherPid = [int]$state.pid
            $scriptPath = [IO.Path]::GetFullPath([string]$state.script)
            if (-not [IO.Path]::IsPathRooted([string]$state.script) -or
                -not $scriptPath.Equals($expectedScript, [StringComparison]::OrdinalIgnoreCase)) { return $null }
            $epoch = [DateTime]::SpecifyKind([DateTime]'1970-01-01', [DateTimeKind]::Utc)
            $expectedStarted = $epoch.AddSeconds([double]$state.started_at)
            if (Test-Path -LiteralPath $pidPath) {
                $pidText = (Get-Content -LiteralPath $pidPath -Raw -Encoding UTF8).Trim()
                if ($pidText -notmatch '^\d+$' -or [int]$pidText -ne $launcherPid) { return $null }
            }
            $stateValidated = $true
        } elseif (Test-Path -LiteralPath $pidPath) {
            $pidText = (Get-Content -LiteralPath $pidPath -Raw -Encoding UTF8).Trim()
            if ($pidText -notmatch '^\d+$') { return $null }
            $launcherPid = [int]$pidText
            $expectedStarted = (Get-Item -LiteralPath $pidPath).LastWriteTimeUtc
        } else { return $null }
        if ($launcherPid -le 0) { return $null }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $launcherPid" -ErrorAction Stop
        if (-not $process -or $process.Name -notmatch '^python(?:w|[0-9.]+)?\.exe$' -or
            [string]::IsNullOrWhiteSpace($process.CommandLine)) { return $null }
        $created = ([DateTime]$process.CreationDate).ToUniversalTime()
        if ([Math]::Abs(($created - $expectedStarted).TotalSeconds) -gt 15) { return $null }
        $arguments = [SocketRoomWindowsArguments]::Split($process.CommandLine)
        if ($arguments.Length -lt 2) { return $null }
        $scriptIndex = 1
        while ($scriptIndex -lt $arguments.Length) {
            $argument = $arguments[$scriptIndex]
            if ($argument -eq '--') { $scriptIndex++; break }
            if (-not $argument.StartsWith('-')) { break }
            # Python executes code/modules instead of a script for these switches.
            if ($argument.StartsWith('-c', [StringComparison]::Ordinal) -or
                $argument.StartsWith('-m', [StringComparison]::Ordinal)) { return $null }
            if ($argument -eq '-X' -or $argument -eq '-W' -or $argument -eq '--check-hash-based-pycs') {
                if ($scriptIndex + 1 -ge $arguments.Length) { return $null }
                $scriptIndex += 2
                continue
            }
            if (($argument.StartsWith('-X', [StringComparison]::Ordinal) -or
                 $argument.StartsWith('-W', [StringComparison]::Ordinal)) -and $argument.Length -gt 2) {
                $scriptIndex++
                continue
            }
            if ($argument -cmatch '^-[bBdeEiIOPqsSuvx]+$') {
                $scriptIndex++
                continue
            }
            return $null
        }
        if ($scriptIndex -ge $arguments.Length) { return $null }
        $scriptArgument = $arguments[$scriptIndex]
        if ([IO.Path]::IsPathRooted($scriptArgument)) {
            $argumentPath = [IO.Path]::GetFullPath($scriptArgument)
        } elseif ($stateValidated) {
            $argumentPath = [IO.Path]::GetFullPath((Join-Path $projectPath $scriptArgument))
        } else { return $null }
        if (-not $argumentPath.Equals($expectedScript, [StringComparison]::OrdinalIgnoreCase)) { return $null }
        return [PSCustomObject]@{ Id = $launcherPid; CreatedUtc = $created; Process = $process; Script = $expectedScript }
    } catch { return $null }
}

function Remove-SocketRoomMarkers {
    param([Parameter(Mandatory=$true)][string]$Project)
    foreach ($name in @('public_link.txt', 'launcher.pid', 'launcher_state.json')) {
        Remove-Item -LiteralPath (Join-Path $Project $name) -Force -ErrorAction SilentlyContinue
    }
}

function Test-SocketRoomHealth {
    param([Parameter(Mandatory=$true)][string]$BaseUrl, [int]$TimeoutSeconds = 5)
    try {
        $health = Invoke-RestMethod -Uri ($BaseUrl.TrimEnd('/') + '/api/health') -Method Get -TimeoutSec $TimeoutSeconds -ErrorAction Stop
        return ($null -ne $health -and $health.status -eq 'ok')
    } catch { return $false }
}

function Get-SocketRoomPublicLink {
    param([Parameter(Mandatory=$true)][string]$Project)
    $path = Join-Path $Project 'public_link.txt'
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $link = (Get-Content -LiteralPath $path -Raw -Encoding UTF8).Trim()
        if ($link -cmatch '^https://[a-z0-9-]+\.trycloudflare\.com/?$') { return $link.TrimEnd('/') }
    } catch { }
    return $null
}
