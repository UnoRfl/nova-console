# Run every 5 minutes by NovaLaunch.vbs (the "Nova" self-heal task).
# The Discord bot (~/nova-bot) and its supervisor have died silently more than
# once with nothing in any log. If NEITHER the bot nor its supervisor is
# running, start it again through its own scheduled task. If the bot is alive
# but its supervisor is gone, leave it alone: starting a supervisor then would
# run a second bot and every message would be answered twice.
$ErrorActionPreference = 'SilentlyContinue'
$botDir = Join-Path $env:USERPROFILE 'nova-bot'
if (-not (Test-Path (Join-Path $botDir 'bot.py'))) { exit 0 }
$log = Join-Path $env:USERPROFILE '.nova\ensure-bot.log'

$procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe' OR Name='powershell.exe'"
# The bot is launched as plain "pythonw.exe bot.py" from its folder, so its
# command line does NOT contain "nova-bot" (matching on that once missed a
# live bot and started duplicates).
$bot = $procs | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match '(^|[\\/"\s])bot\.py"?\s*$' }
$sup = $procs | Where-Object { $_.CommandLine -like '*nova-bot*watch.ps1*' }
if ($bot -or $sup) { exit 0 }

Add-Content -Path $log -Value ("{0}  bot and supervisor both gone -- starting NovaDiscordBot" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
& schtasks.exe /run /tn NovaDiscordBot | Out-Null
