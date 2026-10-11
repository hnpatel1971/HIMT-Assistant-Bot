# Installs or updates the HIMT Assistant Bot on Windows Server 2022 with IIS,
# next to HIMT-LMS on the same server (or on its own).
#
# Run in PowerShell as Administrator:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# The first run installs the software (IIS modules, Git, Python 3.12, Node.js,
# MongoDB, NSSM), creates C:\himt-assistant-bot-config\himt-assistant-bot.env
# and stops so you can fill in the keys. The second run builds the chat widget,
# installs the backend, starts the HIMT-Assistant-Bot Windows service and adds
# an IIS site for the bot's host name. Run it again any time to update to the
# latest code. See docs/deploy-windows.md.

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
$ProgressPreference = "SilentlyContinue"

$AppDir = "C:\himt-assistant-bot"
$ConfigDir = "C:\himt-assistant-bot-config"
$EnvFile = Join-Path $ConfigDir "himt-assistant-bot.env"
$SiteDir = "C:\inetpub\himt-assistant-bot"
$Repo = "https://github.com/hnpatel1971/HIMT-Assistant-Bot.git"
$Branch = "main"
$ServiceName = "HIMT-Assistant-Bot"
$SiteName = "himt-assistant-bot"
$Port = 8001
$VenvPython = "$AppDir\backend\.venv\Scripts\python.exe"

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

function Add-MachinePath($dir) {
  $current = [Environment]::GetEnvironmentVariable("Path", "Machine")
  if (($current -split ";") -notcontains $dir) {
    [Environment]::SetEnvironmentVariable("Path", "$current;$dir", "Machine")
  }
  Refresh-Path
}

function Has($command) { [bool](Get-Command $command -ErrorAction SilentlyContinue) }

function Run($exe, [string[]]$arguments) {
  & $exe @arguments
  if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 3010) {
    throw "'$exe $($arguments -join ' ')' failed with exit code $LASTEXITCODE"
  }
}

function New-Secret {
  $bytes = New-Object byte[] 32
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
}

function Read-EnvFile {
  $values = [ordered]@{}
  foreach ($line in Get-Content $EnvFile) {
    $text = $line.Trim()
    if (-not $text -or $text.StartsWith("#")) { continue }
    $split = $text.IndexOf("=")
    if ($split -lt 1) { continue }
    $value = $text.Substring($split + 1).Trim()
    # Accept values wrapped in quotes; the quotes are not part of the value.
    if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    $values[$text.Substring(0, $split).Trim()] = $value
  }
  $values
}

# --- Settings file ---

if (-not (Test-Path $EnvFile)) {
  Step "Creating $EnvFile"
  New-Item -ItemType Directory -Force $ConfigDir | Out-Null
  # Only Administrators and the service account may read the keys.
  & icacls $ConfigDir /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" | Out-Null
  $template = @"
# HIMT Assistant Bot settings, one NAME=value per line. Lines starting with # are ignored.
# Replace every FILL_ME, save, and run install.ps1 again.

# Without a domain, leave BOT_HOSTNAME empty: the bot is served on this
# server's IP address at port BOT_PORT, for example http://203.0.113.10:8081/.
# With a domain (for example chat.himtmarine.com, DNS pointing at this server),
# set it here: the bot is then served on port 80/443 for that name only.
BOT_HOSTNAME=
BOT_PORT=8081

# Claude API key from https://console.anthropic.com/settings/keys
ANTHROPIC_API_KEY=FILL_ME
LLM_MODEL=claude-opus-5-5
LLM_EFFORT=low

# Sent as the X-Admin-Token header to use /api/admin/*. Generated; change if you like.
ADMIN_TOKEN=$(New-Secret)

MONGO_URL=mongodb://127.0.0.1:27017
DB_NAME=himt_chatbot
CORS_ORIGINS=*
WEB_DIST_DIR=$AppDir\frontend\build

# Websites the bot learns from, and the daily re-crawl hour (UTC).
HIMT_WEBSITE_URL=https://himtmarine.com
HIMT_ADDITIONAL_SITES=
DAILY_REINGEST_HOUR=2

# Weak-answer email alerts through Resend. Leave empty to skip.
RESEND_API_KEY=
RESEND_FROM_EMAIL=onboarding@resend.dev
WEAK_ANSWER_ALERT_TO=
WEAK_ANSWER_ALERT_MIN_INTERVAL_SEC=120
"@
  Set-Content -Path $EnvFile -Value $template -Encoding ASCII
}
$config = Read-EnvFile

# --- Software ---

Step "Turning on IIS"
Install-WindowsFeature Web-Server -IncludeManagementTools | Out-Null

if (-not (Has choco)) {
  Step "Installing Chocolatey (Windows package installer)"
  Set-ExecutionPolicy Bypass -Scope Process -Force
  Invoke-Expression ((New-Object Net.WebClient).DownloadString("https://community.chocolatey.org/install.ps1"))
  Refresh-Path
}

Step "Installing Git, NSSM and the IIS proxy modules"
Run choco @("install", "-y", "--no-progress", "git", "nssm", "urlrewrite", "iis-arr")
Add-MachinePath "C:\Program Files\Git\cmd"

if (-not (Test-Path "C:\Python312\python.exe")) {
  Step "Installing Python 3.12"
  Run choco @("install", "-y", "--no-progress", "python312")
}
$Python = "C:\Python312\python.exe"

if (-not (Has node)) {
  Step "Installing Node.js"
  Run choco @("install", "-y", "--no-progress", "nodejs-lts")
  Refresh-Path
}

if (-not (Get-Service MongoDB -ErrorAction SilentlyContinue)) {
  Step "Installing MongoDB"
  Run choco @("install", "-y", "--no-progress", "mongodb")
}
Set-Service MongoDB -StartupType Automatic
if ((Get-Service MongoDB).Status -ne "Running") { Start-Service MongoDB }

Step "Checking the tools"
$missing = @("git", "node", "nssm") | Where-Object { -not (Has $_) }
if (-not (Test-Path $Python)) { $missing += "python" }
if ($missing) { throw "Not found after install: $($missing -join ', '). Restart the server and run install.ps1 again." }
Write-Host "All tools found."

# --- Settings check ---

if ((Get-Content $EnvFile) -match "=FILL_ME") {
  Write-Host ""
  Write-Host "Software is installed. Now fill in the keys in $EnvFile" -ForegroundColor Yellow
  Write-Host "(Notepad is opening it), save, and run install.ps1 again." -ForegroundColor Yellow
  Write-Host "Still to fill in:" -ForegroundColor Yellow
  (Get-Content $EnvFile) -match "=FILL_ME" | ForEach-Object { Write-Host "  $($_.Split('=')[0])" -ForegroundColor Yellow }
  Start-Process notepad.exe $EnvFile
  exit 0
}
$hostName = $config["BOT_HOSTNAME"]
$publicPort = if ($hostName) { 80 } elseif ($config["BOT_PORT"]) { [int]$config["BOT_PORT"] } else { 8081 }
if (-not $hostName -and $publicPort -in 80, 443, 8080, $Port) { throw "BOT_PORT $publicPort is already used by the LMS or the bot service; pick another, for example 8081." }

# --- Code ---

Step "Getting the latest code"
if (Test-Path "$AppDir\.git") {
  Run git @("-C", $AppDir, "fetch", "origin", $Branch)
  Run git @("-C", $AppDir, "checkout", $Branch)
  Run git @("-C", $AppDir, "merge", "--ff-only", "origin/$Branch")
} else {
  Run git @("clone", "--branch", $Branch, $Repo, $AppDir)
}

Step "Building the chat widget"
$env:COREPACK_ENABLE_DOWNLOAD_PROMPT = "0"
Run corepack @("enable")
Refresh-Path
Push-Location "$AppDir\frontend"
try {
  Remove-Item Env:NODE_ENV -ErrorAction SilentlyContinue
  Run corepack @("yarn", "install", "--frozen-lockfile", "--non-interactive")
  # No REACT_APP_BACKEND_URL: the widget calls /api on the address it is served from.
  Remove-Item Env:REACT_APP_BACKEND_URL -ErrorAction SilentlyContinue
  $env:NODE_ENV = "production"
  $env:CI = "false"
  Run corepack @("yarn", "build")
} finally {
  Pop-Location
  Remove-Item Env:NODE_ENV -ErrorAction SilentlyContinue
}

Step "Installing the backend"
if (-not (Test-Path $VenvPython)) { Run $Python @("-m", "venv", "$AppDir\backend\.venv") }
Run $VenvPython @("-m", "pip", "install", "--upgrade", "pip")
Run $VenvPython @("-m", "pip", "install", "-r", "$AppDir\backend\requirements.txt")

# --- Windows service ---

Step "Setting up the $ServiceName service"
New-Item -ItemType Directory -Force "$AppDir\logs" | Out-Null
if (-not (Get-Service $ServiceName -ErrorAction SilentlyContinue)) {
  Run nssm @("install", $ServiceName, $VenvPython)
}
# One worker: the daily re-crawl scheduler runs inside the process.
Run nssm @("set", $ServiceName, "AppParameters", "-m uvicorn server:app --host 127.0.0.1 --port $Port --proxy-headers")
Run nssm @("set", $ServiceName, "AppDirectory", "$AppDir\backend")
Run nssm @("set", $ServiceName, "AppStdout", "$AppDir\logs\service.log")
Run nssm @("set", $ServiceName, "AppStderr", "$AppDir\logs\service.log")
Run nssm @("set", $ServiceName, "AppRotateFiles", "1")
Run nssm @("set", $ServiceName, "AppRotateBytes", "10485760")
Run nssm @("set", $ServiceName, "Start", "SERVICE_AUTO_START")
Run nssm @("set", $ServiceName, "DependOnService", "MongoDB")
# Written to the registry directly so values with quotes or spaces survive.
$environment = @($config.GetEnumerator() | Where-Object { $_.Value -ne "" } | ForEach-Object { "$($_.Key)=$($_.Value)" })
New-ItemProperty -Force -Path "HKLM:\SYSTEM\CurrentControlSet\Services\$ServiceName\Parameters" -Name AppEnvironmentExtra -PropertyType MultiString -Value $environment | Out-Null
Restart-Service $ServiceName -ErrorAction SilentlyContinue
if ((Get-Service $ServiceName).Status -ne "Running") { Start-Service $ServiceName }

$healthy = $false
for ($i = 0; $i -lt 30 -and -not $healthy; $i++) {
  Start-Sleep -Seconds 2
  try { $healthy = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/health").StatusCode -eq 200 } catch { }
}
if (-not $healthy) { throw "The bot did not start. See $AppDir\logs\service.log" }
Write-Host "The bot is running on port $Port."

# --- IIS ---

Step "Pointing IIS at the bot"
Import-Module WebAdministration
$appcmd = "$env:windir\system32\inetsrv\appcmd.exe"
# Same server-wide proxy settings HIMT-LMS uses, so running either script keeps both working.
Run $appcmd @("set", "config", "-section:system.webServer/proxy", "/enabled:True", "/timeout:00:10:00", "/preserveHostHeader:True", "/reverseRewriteHostInResponseHeaders:False", "/commit:apphost")
$allowed = & $appcmd list config -section:system.webServer/rewrite/allowedServerVariables
if (-not ($allowed -match "HTTP_X_FORWARDED_PROTO")) {
  Run $appcmd @("set", "config", "-section:system.webServer/rewrite/allowedServerVariables", "/+[name='HTTP_X_FORWARDED_PROTO']", "/commit:apphost")
}
New-Item -ItemType Directory -Force $SiteDir | Out-Null
Copy-Item -Force "$AppDir\deploy\windows\web.config" "$SiteDir\web.config"
# Either a host-name binding on port 80 or its own port on the IP address, so the
# LMS site keeps every other request on port 80.
$site = Get-Website | Where-Object { $_.Name -eq $SiteName }
if (-not $site) {
  New-Website -Name $SiteName -Port $publicPort -HostHeader $hostName -PhysicalPath $SiteDir | Out-Null
} elseif (-not (Get-WebBinding -Name $SiteName -Protocol http -Port $publicPort -HostHeader $hostName)) {
  Get-WebBinding -Name $SiteName -Protocol http | Remove-WebBinding
  New-WebBinding -Name $SiteName -Protocol http -Port $publicPort -HostHeader $hostName
}
Start-Website $SiteName

$ports = if ($hostName) { @(80, 443) } else { @($publicPort) }
foreach ($port in $ports) {
  if (-not (Get-NetFirewallRule -DisplayName "HIMT Assistant Bot $port" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "HIMT Assistant Bot $port" -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow | Out-Null
  }
}

$viaIis = $false
$headers = if ($hostName) { @{ Host = $hostName } } else { @{} }
try { $viaIis = (Invoke-WebRequest -UseBasicParsing -Headers $headers "http://localhost:$publicPort/api/health").StatusCode -eq 200 } catch { }
if (-not $viaIis) { throw "IIS is not passing requests to the bot. Check that URL Rewrite and ARR are installed, then run again." }

Write-Host ""
if ($hostName) {
  Write-Host "Done. Once $hostName points at this server, open http://$hostName/ in a browser." -ForegroundColor Green
  Write-Host "Add an HTTPS certificate for $hostName (docs/deploy-windows.md), then embed:" -ForegroundColor Green
  Write-Host "  <script src=`"https://$hostName/embed.js`" async></script>" -ForegroundColor Green
} else {
  Write-Host "Done. Open http://<this server's IP address>:$publicPort/ in a browser to try the bot." -ForegroundColor Green
  Write-Host "Embedding on an https:// website needs a domain name and certificate (docs/deploy-windows.md)." -ForegroundColor Green
}
