<#
.SYNOPSIS
  Set up and start Clone My Professor (CMP) on Windows. Safe to run again: it updates and starts.

.DESCRIPTION
  Checks for Git and Docker (with -Install it installs them via winget), fetches the project if
  needed, creates .env with random passwords and secrets, builds and starts all containers
  (vLLM too when an NVIDIA GPU is present), downloads the AI models and waits until CMP answers.
  With -WithMoodle it also starts a local test Moodle connected to CMP; with -WithTracing,
  Langfuse for tracing every answer (once chosen, it stays on in later runs).

.EXAMPLE
  .\setup.ps1                  # check, set up, start
  .\setup.ps1 -Install         # also install missing Git / Docker Desktop (needs admin rights)
  .\setup.ps1 -WithMoodle      # plus a local test Moodle
  .\setup.ps1 -WithTracing     # plus Langfuse: a trace of every answer (http://127.0.0.1:3000)
  .\setup.ps1 -Check           # then run the smoke test (10 questions; ~12 min on a CPU)
  .\setup.ps1 -Benchmark       # then the internal benchmark on the courses in data\ (docs/GPU-SETUP.md)
  .\setup.ps1 -Benchmark -BenchmarkArgs "--retrieval-only"   # options of scripts.benchmark
  .\setup.ps1 -Stop            # stop everything (data is kept)
  .\setup.ps1 -Status
#>
param(
    [switch]$Install,
    [switch]$WithMoodle,
    [switch]$WithTracing,
    [switch]$Check,
    [switch]$Benchmark,
    [string]$BenchmarkArgs = "",
    [switch]$Stop,
    [switch]$Status,
    [string]$Site = "localhost",
    [switch]$NoDevSignIn,
    [string]$Dir = "clone-my-professor"
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/rkarlovic/clone-my-professor.git"

function Say($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Warn($text) { Write-Host "!!  $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host "xx  $text" -ForegroundColor Red; exit 1 }
function Has($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function New-Secret([int]$bytes = 36) {
    $b = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    ([Convert]::ToBase64String($b)).Replace('+', '-').Replace('/', '_').TrimEnd('=')
}

# --- 1. tools --------------------------------------------------------------------------------
function Ensure-Tool($cmd, $wingetId, $guide) {
    if (Has $cmd) { return }
    if (-not $Install) {
        Warn "$cmd is missing. $guide"
        Warn "Or run this script with -Install to install it automatically."
        exit 1
    }
    if (-not (Has "winget")) { Fail "winget is not available; install $cmd by hand: $guide" }
    Say "Installing $wingetId (winget)..."
    winget install --id $wingetId -e --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Has $cmd)) { Fail "$cmd was installed but is not on PATH yet: open a new terminal and run this script again." }
}

Ensure-Tool "git" "Git.Git" "Download it from https://git-scm.com/download/win"
Ensure-Tool "docker" "Docker.DockerDesktop" "Install Docker Desktop: https://docs.docker.com/desktop/setup/install/windows-install/ (it needs WSL 2; a restart may be required)"

# --- 2. the project ----------------------------------------------------------------------------
$Root = $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "infra\docker-compose.yml"))) {
    if (-not (Test-Path (Join-Path $Dir "infra\docker-compose.yml"))) {
        Say "Fetching the project into $Dir ..."
        git clone $RepoUrl $Dir
        if ($LASTEXITCODE -ne 0) { Fail "git clone failed" }
    }
    $Root = (Resolve-Path $Dir).Path
}
Set-Location $Root

# --- 3. Docker running? -----------------------------------------------------------------------
# Runs a native program, ignoring its stderr (Windows PowerShell 5.1 turns that into an error
# under "Stop"); returns its output as text.
function Quiet {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $exe, $rest = $args; (& $exe @rest 2>$null | ForEach-Object { "$_" }) -join "`n" } finally { $ErrorActionPreference = $old }
}
function Start-Docker {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { return }
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $desktop) {
        Say "Starting Docker Desktop..."
        Start-Process $desktop
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Seconds 5
            docker info *> $null
            if ($LASTEXITCODE -eq 0) { break }
        }
    }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Start Docker Desktop and run this script again." }
}
Start-Docker

# --- 3b. the graphics card: which models file fits its memory ----------------------------------
# 24 GB+: Qwen 3.5 9B (full precision), Ollama on the GPU too; 14-23 GB: the 9B with 4-bit weights;
# 7-13 GB: the 4B with 4-bit weights, embeddings on the CPU; smaller or none: everything on the CPU.
$gpu = $false; $gpuOllama = $false; $vramMb = 0; $modelsFile = "models.yaml"
if (Has "nvidia-smi") {
    $q = Quiet nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits
    if ($LASTEXITCODE -eq 0 -and $q -match "^\s*(\d+)") { $vramMb = [int]$Matches[1] }
}
if ($vramMb -ge 22000) { $gpu = $true; $gpuOllama = $true; $modelsFile = "models.vllm.yaml" }
elseif ($vramMb -ge 14000) { $gpu = $true; $modelsFile = "models.vllm-16gb.yaml" }
elseif ($vramMb -ge 7000) { $gpu = $true; $modelsFile = "models.vllm-8gb.yaml" }
elseif ($vramMb -gt 0) { Warn "The NVIDIA card has only $vramMb MB of memory, too little for vLLM: the models run on the CPU." }
if ($gpu) { Say ("NVIDIA GPU with {0:N1} GB of memory: config\$modelsFile" -f ($vramMb / 1024)) }

# Docker Desktop gets half of the PC's memory by default; vLLM, the document reader and the
# database need about 11 GB together. The limit lives in .wslconfig in the Windows user folder.
if ($gpu) {
    $dockerGb = [math]::Round([double](Quiet docker info --format "{{.MemTotal}}") / 1GB, 1)
    $hostGb = [math]::Round([double](Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
    $wantGb = [math]::Min(12, $hostGb - 4)
    $wslConfig = Join-Path $env:USERPROFILE ".wslconfig"
    if ($dockerGb -lt 11 -and $wantGb -gt $dockerGb) {
        Warn "Docker may use only $dockerGb GB of this PC's $hostGb GB; with the GPU models it needs about 11 GB."
        Write-Host "    That limit is set in the file $wslConfig"
        if (Test-Path $wslConfig) {
            Write-Host "    The file exists: open it in Notepad, put these two lines in it (or change the memory line):"
            Write-Host "        [wsl2]"
            Write-Host "        memory=${wantGb}GB"
            Write-Host "    save it, then run:  wsl --shutdown   and this script again."
        } else {
            $answer = "n"
            try { $answer = Read-Host "    Create it with memory=${wantGb}GB and restart Docker now? [Y/n]" } catch { }
            if ($answer -notmatch "^[nN]") {
                [IO.File]::WriteAllText($wslConfig, "[wsl2]`r`nmemory=${wantGb}GB`r`n", (New-Object System.Text.UTF8Encoding $false))
                Say "Wrote $wslConfig; restarting Docker..."
                Get-Process "Docker Desktop" -ErrorAction SilentlyContinue | Stop-Process -Force
                $null = Quiet wsl --shutdown
                Start-Sleep -Seconds 5
                Start-Docker
                $dockerGb = [math]::Round([double](Quiet docker info --format "{{.MemTotal}}") / 1GB, 1)
                Say "Docker may now use $dockerGb GB."
            } else {
                Write-Host "    Without it vLLM may stop for lack of memory. To do it later, create that file with:"
                Write-Host "        [wsl2]"
                Write-Host "        memory=${wantGb}GB"
            }
        }
    }
}

# --- compose command ----------------------------------------------------------------------------
$Compose = @("compose", "-f", "infra/docker-compose.yml")
if ($gpu) { $Compose += @("-f", "infra/docker-compose.gpu.yml") }
if ($gpuOllama) { $Compose += @("-f", "infra/docker-compose.gpu-ollama.yml") }
$Compose += @("--env-file", ".env")
$tracing = $WithTracing -or ((Test-Path ".env") -and (Select-String -Path ".env" -Pattern "^LANGFUSE_SALT=" -Quiet))
if ($tracing) { $Compose = $Compose[0..2] + @("-f", "infra/docker-compose.langfuse.yml") + $Compose[3..($Compose.Length - 1)] }
if ($WithMoodle) { $Compose += @("--profile", "moodle") }
function Dc { & docker @Compose @args; if ($LASTEXITCODE -ne 0) { Fail "docker $($args -join ' ') failed" } }
# A docker call whose failure is expected or tolerated (Windows PowerShell 5.1 turns a native
# program's stderr into an error under "Stop"); returns its output as text.
function DcQuiet {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { (& docker @Compose @args 2>&1 | ForEach-Object { "$_" }) -join "`n" } finally { $ErrorActionPreference = $old }
}

if ($Stop) { Say "Stopping CMP (data is kept)..."; & docker @Compose --profile moodle stop; exit 0 }
if ($Status) { & docker @Compose --profile moodle ps; exit 0 }

# --- 4. .env -------------------------------------------------------------------------------------
$EnvPath = Join-Path $Root ".env"
$utf8 = New-Object System.Text.UTF8Encoding $false
if (-not (Test-Path $EnvPath)) {
    Say "Creating .env with new random passwords and secrets..."
    $text = [IO.File]::ReadAllText((Join-Path $Root ".env.example"))
    foreach ($placeholder in @("change-me-owner", "change-me-app", "change-me-moodle-db")) {
        $text = $text.Replace($placeholder, (New-Secret 24))
    }
    $text = $text.Replace("change-me-Admin-1!", (New-Secret 18) + "aA1!")
    $text = $text.Replace("change-me-Test-1!", (New-Secret 18) + "aA1!")
    [IO.File]::WriteAllText($EnvPath, $text, $utf8)
}
function Get-EnvValue($key) {
    foreach ($line in [IO.File]::ReadAllLines($EnvPath)) {
        if ($line -match "^$key=(.*)$") { return $Matches[1] }
    }
    return $null
}
function Set-EnvValue($key, $value) {
    $lines = [System.Collections.Generic.List[string]]([IO.File]::ReadAllLines($EnvPath))
    $done = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$key=") { $lines[$i] = "$key=$value"; $done = $true }
    }
    if (-not $done) { $lines.Add("$key=$value") }
    [IO.File]::WriteAllText($EnvPath, ($lines -join "`n") + "`n", $utf8)
}
if (-not (Get-EnvValue "SESSION_SECRET")) { Set-EnvValue "SESSION_SECRET" (New-Secret 48) }
if (-not (Get-EnvValue "ADMIN_TOKEN")) { Set-EnvValue "ADMIN_TOKEN" (New-Secret 36) }
if (-not (Get-EnvValue "CMP_SITE")) { Set-EnvValue "CMP_SITE" $Site }
# A real host name: HTTPS with an automatic certificate. localhost: plain http (a secure context
# for browsers anyway), so a local trial shows no certificate warning.
$siteName = Get-EnvValue "CMP_SITE"
if ($siteName -eq "localhost") { Set-EnvValue "CMP_SCHEME" "http"; Set-EnvValue "CMP_SITE_ADDRESS" "http://localhost" }
else { Set-EnvValue "CMP_SCHEME" "https"; Set-EnvValue "CMP_SITE_ADDRESS" $siteName }
$scheme = Get-EnvValue "CMP_SCHEME"
if (-not (Get-EnvValue "AUTH_MODE")) {
    # Name-only sign-in is for trying CMP on this computer; a real server uses LTI / SSO only.
    $mode = if ($Site -eq "localhost" -and -not $NoDevSignIn) { "dev" } else { "prod" }
    Set-EnvValue "AUTH_MODE" $mode
}
if ($tracing) {
    # Langfuse's own passwords and the API keys CMP sends traces with (created in Langfuse on
    # its first start). Only missing ones are added, so a rerun keeps existing data readable.
    $hex = { param($n) $b = New-Object byte[] $n; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString("x2") }) }
    $want = [ordered]@{
        LANGFUSE_PUBLIC_KEY = "pk-lf-" + (& $hex 16)
        LANGFUSE_SECRET_KEY = "sk-lf-" + (& $hex 24)
        LANGFUSE_DB_PASSWORD = (& $hex 18)
        LANGFUSE_SALT = (& $hex 24)
        LANGFUSE_ENCRYPTION_KEY = (& $hex 32)
        LANGFUSE_NEXTAUTH_SECRET = (& $hex 32)
        LANGFUSE_CLICKHOUSE_PASSWORD = (& $hex 18)
        LANGFUSE_MINIO_PASSWORD = (& $hex 18)
        LANGFUSE_REDIS_PASSWORD = (& $hex 18)
        LANGFUSE_ADMIN_PASSWORD = (& $hex 12) + "Aa1!"
    }
    foreach ($k in $want.Keys) { if (-not (Get-EnvValue $k)) { Set-EnvValue $k $want[$k] } }
}
# The models file for this computer (3b) and, with a GPU, how vLLM starts its model.
Set-EnvValue "CMP_MODELS_FILE" $modelsFile
if ($gpu) {
    $cfgPath = Join-Path $Root "config\$modelsFile"
    $serve = (Select-String -Path $cfgPath -Pattern '^serve_model:\s*(\S+)').Matches[0].Groups[1].Value
    $serveArgs = (Select-String -Path $cfgPath -Pattern '^serve_args:\s*"([^"]*)"').Matches[0].Groups[1].Value
    Set-EnvValue "VLLM_MODEL" $serve
    Set-EnvValue "VLLM_ARGS" $serveArgs
}

# --- 5. build and start --------------------------------------------------------------------------
Say ("Building and starting CMP" + $(if ($gpu) { " (vLLM serves $(Get-EnvValue "VLLM_MODEL"); the first start downloads it)" } else { " (no NVIDIA GPU: models run on the CPU)" }) + "...")
Dc up -d --build

# --- 6. models -----------------------------------------------------------------------------------
# The api container reads the active models file (models.vllm.yaml with a GPU) and pulls every
# Ollama model it names that is missing; vLLM downloads its own model (VLLM_MODEL) on start.
Say "Checking the AI models (missing ones are downloaded once; can take a while)..."
Dc exec -T api python -m scripts.models --pull

# --- 7. wait until CMP answers --------------------------------------------------------------------
Say "Waiting for ${scheme}://$siteName ..."
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    $code = & curl.exe -k -s -o NUL -w "%{http_code}" "${scheme}://$siteName/health"
    if ($code -eq "200") { $ok = $true; break }
    Start-Sleep -Seconds 5
}
if (-not $ok) { Warn "CMP did not answer yet; check with: .\setup.ps1 -Status" }

# --- 8. optional: a local test Moodle ---------------------------------------------------------------
if ($WithMoodle) {
    $moodlePort = if (Get-EnvValue "CMP_MOODLE_PORT") { Get-EnvValue "CMP_MOODLE_PORT" } else { "8080" }
    Say "Setting up the local Moodle (http://127.0.0.1:$moodlePort)..."
    for ($i = 0; $i -lt 30; $i++) {  # wait until Moodle's web server answers
        $code = & curl.exe -s -o NUL -w "%{http_code}" "http://127.0.0.1:$moodlePort/login/index.php"
        if ($code -match "^(200|303)$") { break }
        Start-Sleep -Seconds 3
    }
    $null = DcQuiet exec -T -u www-data moodle sh /opt/cmp/install.sh
    $json = DcQuiet exec -T -u www-data moodle php /opt/cmp/setup_cmp.php
    if ($json.IndexOf("{") -lt 0) { Fail "the Moodle setup script gave no result: $json" }
    $info = ($json.Substring($json.IndexOf("{")) | ConvertFrom-Json)
    Set-EnvValue "TEST_MOODLE_WS_TOKEN" $info.ws_token
    Set-EnvValue "CMP_FRAME_ANCESTORS" "http://127.0.0.1:$moodlePort"
    Dc up -d --force-recreate api worker web
    Dc exec -T api python -m scripts.lti_platform add --name "Moodle (local test)" --issuer $info.issuer --client-id $info.client_id --auth-url $info.auth_url --jwks-url "http://moodle/mod/lti/certs.php" --deployment-id $info.deployment_id
    Dc exec -T api python -m scripts.lti_platform ws --issuer $info.issuer --base-url "http://moodle" --token-env TEST_MOODLE_WS_TOKEN
}


# --- 9. optional: smoke test ------------------------------------------------------------------------
if ($Check) {
    Say "Smoke test: CMP's own small course and 10 questions (about 12 minutes on a CPU, seconds on a GPU)..."
    & docker @Compose exec -T api python -m scripts.smoketest
    if ($LASTEXITCODE -ne 0) { Warn "The smoke test did not pass; see the lines marked FAIL above." }
}
# --- 10. optional: the internal benchmark (course folders in data\, sets in eval\sets\) -------------
if ($Benchmark) {
    Say "Benchmark: every course in data\ with a question set; summary in eval\reports\ (hours on a CPU)..."
    $extra = if ($BenchmarkArgs) { $BenchmarkArgs -split '\s+' } else { @() }
    & docker @Compose run --rm benchmark python -m scripts.benchmark @extra
    if ($LASTEXITCODE -ne 0) { Warn "The benchmark reported failed steps; see 'Failed' at the end of its output." }
}
# --- done -------------------------------------------------------------------------------------------
Write-Host ""
Say "CMP is running: ${scheme}://$siteName"
if ((Get-EnvValue "AUTH_MODE") -eq "dev") { Write-Host "    Sign in on the start page with any name (test sign-in, this computer only)." }
Write-Host "    Admin page: ${scheme}://$siteName/#/admin  (key: ADMIN_TOKEN in .env)"
if ($WithMoodle) {
    Write-Host "    Test Moodle: http://127.0.0.1:$moodlePort  users teacher1 / student1, password: MOODLE_TEST_PASSWORD in .env"
    Write-Host "    Open the course 'Rad s darovitom djecom (Moodle demo)' and the activity 'Asistent (CMP)'."
}
if ($tracing) {
    $lfPort = Get-EnvValue "LANGFUSE_PORT"; if (-not $lfPort) { $lfPort = "3000" }
    $lfUser = Get-EnvValue "LANGFUSE_ADMIN_EMAIL"; if (-not $lfUser) { $lfUser = "admin@cmp.local" }
    Write-Host "    Langfuse (traces): http://127.0.0.1:$lfPort  user $lfUser, password: LANGFUSE_ADMIN_PASSWORD in .env"
}
Write-Host "    Stop: .\setup.ps1 -Stop    Start again: .\setup.ps1    Status: .\setup.ps1 -Status"
