param(
    [double]$ThresholdSeconds = 8,
    [int]$DurationSeconds = 0,
    [string]$LogDirectory = (Join-Path $env:LOCALAPPDATA 'wiki-hook-diagnostics')
)

# Observe native hook processes without changing their commands or settings.
# Command lines identify hooks only; payloads and environment are never read.
$ErrorActionPreference = 'Stop'
$directoryHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($LogDirectory).ToLowerInvariant())))
$mutex = [Threading.Mutex]::new($false, "Local\WikiHookTimeoutObserver-$directoryHash")
if (-not $mutex.WaitOne(0)) { $mutex.Dispose(); exit 0 }
$seen = @{}
$started = [DateTime]::UtcNow
$lastCleanup = [DateTime]::MinValue
try {
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    while ($DurationSeconds -eq 0 -or ([DateTime]::UtcNow - $started).TotalSeconds -lt $DurationSeconds) {
        $now = [DateTime]::UtcNow
        try {
            $processes = @(Get-CimInstance Win32_Process -Filter "Name = 'pwsh.exe' OR Name = 'cmd.exe' OR Name = 'python.exe' OR Name = 'curl.exe' OR Name = 'more.com'")
            $alive = @{}
            foreach ($process in $processes) {
                $key = "$($process.ProcessId)-$($process.CreationDate.ToUniversalTime().Ticks)"
                $alive[$key] = $true
                if ($seen.ContainsKey($key)) { continue }
                $elapsed = ($now - $process.CreationDate.ToUniversalTime()).TotalSeconds
                if ($elapsed -lt $ThresholdSeconds) { continue }
                $command = $process.CommandLine
                if ($command -match '-EncodedCommand\s+(\S+)') {
                    try { $command = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($Matches[1])) }
                    catch { continue }
                }
                if ($command -match 'watch_hook|Get-CimInstance|pytest| -c ') { continue }
                if ($command -notmatch '[\\/](hook\.py|host_boundary\.py|codex_pretool\.py|inject\.py|session_state\.py|declared_continuation\.py|sync\.py)(?:[\x22\x27\s]|$)') { continue }
                $hook = $Matches[1]
                $children = @($processes | Where-Object ParentProcessId -eq $process.ProcessId | ForEach-Object {
                    @{pid = $_.ProcessId; name = $_.Name; created_utc = $_.CreationDate.ToUniversalTime().ToString('o')}
                })
                $record = @{
                    hook = $hook; pid = $process.ProcessId; parent_pid = $process.ParentProcessId
                    observed_utc = $now.ToString('o'); started_utc = $process.CreationDate.ToUniversalTime().ToString('o')
                    elapsed_ms = [math]::Round($elapsed * 1000); status = 'alive_past_threshold'
                    cpu_ms = ([double]$process.KernelModeTime + [double]$process.UserModeTime) / 10000
                    working_set_bytes = [long]$process.WorkingSetSize; children = $children
                }
                $path = Join-Path $LogDirectory "process-$key.log"
                $record | ConvertTo-Json -Depth 5 -Compress | Set-Content -LiteralPath $path -Encoding utf8NoBOM
                $seen[$key] = @{path = $path; record = $record}
            }
            foreach ($key in @($seen.Keys)) {
                if ($alive.ContainsKey($key)) { continue }
                $item = $seen[$key]
                $item.record['disappeared_utc'] = $now.ToString('o')
                $item.record['status'] = 'process_disappeared_exit_reason_unknown'
                $item.record | ConvertTo-Json -Depth 5 -Compress | Set-Content -LiteralPath $item.path -Encoding utf8NoBOM
                $seen.Remove($key)
            }
            if (($now - $lastCleanup).TotalHours -ge 1) {
                # 최근 60초 기록은 진행 중일 수 있다. 나머지는 7일·최신 100개까지 보관한다.
                $old = @(Get-ChildItem -LiteralPath $LogDirectory -Filter '*.log' -File |
                    Where-Object LastWriteTimeUtc -lt $now.AddSeconds(-60) | Sort-Object LastWriteTimeUtc -Descending)
                for ($i = 0; $i -lt $old.Count; $i++) {
                    if ($i -ge 100 -or $old[$i].LastWriteTimeUtc -lt $now.AddDays(-7)) {
                        Remove-Item -LiteralPath $old[$i].FullName
                    }
                }
                $lastCleanup = $now
            }
        } catch {
            # 예외 메시지에는 명령 원문이 섞일 수 있으므로 타입만 기록한다.
            "$($now.ToString('o')) $($_.Exception.GetType().Name)" |
                Set-Content -LiteralPath (Join-Path $LogDirectory 'observer-error.log') -Encoding utf8NoBOM
        }
        Start-Sleep -Milliseconds 500
    }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
