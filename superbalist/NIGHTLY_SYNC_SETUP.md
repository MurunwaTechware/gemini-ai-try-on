# Nightly sync — setup (Windows Task Scheduler)

Runs `sync_catalogue.py` against Cloud SQL every night at 3am. The wrapper
script (`run_nightly_sync.ps1`) starts the Auth Proxy, runs the sync, then stops
the proxy — all on its own. If the PC is off at 3am, Windows runs it at the next
wake (catch-up).

---

## 1. One-time prep

### a. Put the proxy somewhere stable
Move the Auth Proxy exe to a fixed path, e.g. `C:\cloud-sql-proxy\cloud-sql-proxy.exe`.

### b. Create the DB-URL file (keeps the password out of the script + out of git)
Create `superbalist\db_url.txt` containing ONE line — the Cloud SQL URL, port 5433:
```
postgresql://postgres:t-%25%5CTtbB.L%29%25zU%286@localhost:5433/ProductsDB
```
Then make sure it's git-ignored (add to `.gitignore`):
```
superbalist/db_url.txt
superbalist/sync_logs/
```

### c. Edit the four paths at the top of `run_nightly_sync.ps1`
Open it and set `$ProxyExe`, `$SyncDir`, `$Python`, `$DbUrlFile` to your machine's
real paths. (`$Instance` and `$ProxyPort` are already correct.)

### d. Test it by hand FIRST (important)
In a terminal:
```
powershell -ExecutionPolicy Bypass -File "C:\Users\USER\gemini-ai-try-on\superbalist\run_nightly_sync.ps1"
```
Watch it: proxy starts → sync runs → proxy stops. Check the log it wrote in
`superbalist\sync_logs\`. Only schedule it once a manual run works end to end.

---

## 2. Create the scheduled task

### Option A — one command (run PowerShell as Administrator)
```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument '-ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\USER\gemini-ai-try-on\superbalist\run_nightly_sync.ps1"'
$trigger = New-ScheduledTaskTrigger -Daily -At 3:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "Superbalist Nightly Sync" `
  -Action $action -Trigger $trigger -Settings $settings `
  -Description "Starts Cloud SQL proxy, syncs the Superbalist catalogue, stops the proxy." `
  -RunLevel Highest
```
`-StartWhenAvailable` is the catch-up: a missed 3am run fires at the next wake.

### Option B — Task Scheduler GUI
1. Open **Task Scheduler** → **Create Task** (not "Basic Task").
2. **General**: name it; tick **Run whether user is logged on or not**;
   **Run with highest privileges**.
3. **Triggers** → New → Daily, 3:00 AM.
4. **Actions** → New → Program: `powershell.exe`,
   Arguments:
   `-ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\USER\gemini-ai-try-on\superbalist\run_nightly_sync.ps1"`
5. **Settings** → tick **Run task as soon as possible after a scheduled start
   is missed** (this is the catch-up), and set **Stop the task if it runs longer
   than 2 hours**.
6. Save (it may ask for your Windows password).

---

## 3. Check it's working

- **Force a test run now:** Task Scheduler → right-click the task → **Run**.
  Then read the newest file in `superbalist\sync_logs\`.
- **See history:** Task Scheduler → the task → **History** tab (shows each fire).
- **Confirm Cloud SQL got fresh data:** with the proxy up,
  `psql $env:DATABASE_URL -c "SELECT max(last_synced) FROM products;"` — should
  be tonight.

---

## Notes

- **The PC must be on (awake) for a run to happen.** Windows can't sync while
  shut down. `-StartWhenAvailable` covers "was off at 3am, catch up on wake",
  but a PC that's never on overnight won't sync until it's next on. If this app
  ever needs guaranteed nightly syncs, move the sync to a small always-on cloud
  VM or Cloud Run job — not a laptop. (Future task.)
- **Logs** auto-trim to the last 30 runs, in `superbalist\sync_logs\`.
- **Stale cleanup** runs as part of the sync: products Superbalist dropped get
  hidden (not deleted) after 7 days unseen, and the sweep is skipped after an
  unhealthy run (< 2000 products) so it can't wipe the catalogue.
