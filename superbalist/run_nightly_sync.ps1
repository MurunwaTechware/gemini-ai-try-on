# ============================================================
#  Nightly Superbalist sync -> Cloud SQL
#  Run by Windows Task Scheduler (3am, catch-up on next wake).
#
#  What it does, in order:
#    1. start the Cloud SQL Auth Proxy (so Cloud SQL is reachable at localhost)
#    2. wait until the proxy is actually listening
#    3. run sync_catalogue.py against Cloud SQL
#    4. ALWAYS stop the proxy afterwards (even if the sync failed)
#    5. write a timestamped log of the whole run
#
#  The database password is NOT in this file. It is read from a sibling file
#  `db_url.txt` (one line: the full DATABASE_URL). Keep that file out of git.
#
#  EDIT THE FOUR PATHS IN THE CONFIG BLOCK to match your machine, then test by
#  running this script by hand once before scheduling it.
# ============================================================

# ---------- CONFIG — edit these four ----------
$ProxyExe   = "C:\cloud-sql-proxy\cloud-sql-proxy.exe"
$Instance   = "project-d045636a-95f8-4566-932:us-east1:post-1"
$SyncDir    = "C:\Users\USER\gemini-ai-try-on\superbalist"   # folder with sync_catalogue.py
$Python     = "python"                                        # or full path to python.exe
# DATABASE_URL is read from this file (one line). Port must match $ProxyPort below.
$DbUrlFile  = "C:\Users\USER\gemini-ai-try-on\superbalist\db_url.txt"
$ProxyPort  = 5433
# ----------------------------------------------

$ErrorActionPreference = "Stop"
$LogDir = Join-Path $SyncDir "sync_logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp  = Get-Date -Format "yyyy-MM-dd_HHmmss"
$Log    = Join-Path $LogDir "sync_$Stamp.log"

function Say($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    $line | Tee-Object -FilePath $Log -Append
}

$proxy = $null
try {
    Say "=== Nightly sync starting ==="

    if (-not (Test-Path $ProxyExe)) { throw "Proxy not found at $ProxyExe" }
    if (-not (Test-Path $DbUrlFile)) { throw "DB url file not found at $DbUrlFile" }
    $env:DATABASE_URL = (Get-Content $DbUrlFile -Raw).Trim()
    if (-not $env:DATABASE_URL) { throw "db_url.txt is empty" }

    # 1. start the proxy (its own window hidden), capture its output to a log
    Say "Starting Auth Proxy on port $ProxyPort ..."
    $proxyLog = Join-Path $LogDir "proxy_$Stamp.log"
    $proxy = Start-Process -FilePath $ProxyExe `
        -ArgumentList @($Instance, "--port", "$ProxyPort") `
        -RedirectStandardError $proxyLog -RedirectStandardOutput "$proxyLog.out" `
        -WindowStyle Hidden -PassThru

    # 2. wait (up to 30s) until something is listening on the proxy port.
    #    A raw TCP socket test — instant and reliable on Windows PowerShell 5.1
    #    (Test-NetConnection is slow/hangs there, so we don't use it).
    function Test-Port($port) {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $ok = $client.ConnectAsync("127.0.0.1", $port).Wait(1000)
            return ($ok -and $client.Connected)
        } catch { return $false }
        finally { $client.Close() }
    }
    $ready = $false
    foreach ($i in 1..30) {
        if (Test-Port $ProxyPort) { $ready = $true; break }
        if ($proxy.HasExited) { throw "Proxy exited early (see $proxyLog)" }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "Proxy did not become ready within 30s (see $proxyLog)" }
    Say "Proxy is listening."

    # 3. run the sync
    Say "Running sync_catalogue.py ..."
    Push-Location $SyncDir
    & $Python "sync_catalogue.py" 2>&1 | Tee-Object -FilePath $Log -Append
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { throw "sync_catalogue.py exited with code $code" }
    Say "Sync finished OK."
}
catch {
    Say "ERROR: $_"
}
finally {
    # 4. always stop the proxy
    if ($proxy -and -not $proxy.HasExited) {
        Say "Stopping Auth Proxy (pid $($proxy.Id)) ..."
        Stop-Process -Id $proxy.Id -Force -ErrorAction SilentlyContinue
    }
    Say "=== Nightly sync done ==="
    # keep only the 30 most recent logs
    Get-ChildItem $LogDir -Filter "sync_*.log" | Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 30 | Remove-Item -ErrorAction SilentlyContinue
}
