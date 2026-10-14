# Keeps main.py and the WhatsApp bridge running, restarting either within 30 s if it stops. Output goes to logs\.
# Started hidden at logon by the "WhatsApp assistant" shortcut in the Startup folder (see README).
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force logs | Out-Null
$env:PYTHONUNBUFFERED = '1'  # so the backend log is written as it happens
$commands = @{
    backend  = '.venv\Scripts\python.exe main.py'
    whatsapp = 'node whatsapp_bridge.js'
}
$running = @{}
while ($true) {
    foreach ($name in $commands.Keys) {
        if (-not $running[$name] -or $running[$name].HasExited) {
            # shortcut: logs are appended forever; delete logs\ by hand if it ever gets big
            Add-Content "logs\$name.log" "=== starting $(Get-Date -Format s) ==="
            $running[$name] = Start-Process cmd.exe "/c $($commands[$name]) >> logs\$name.log 2>&1" -WindowStyle Hidden -PassThru
        }
    }
    Start-Sleep -Seconds 30
}
