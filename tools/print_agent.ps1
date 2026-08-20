# EPIQ print bridge — runs on the office PC.
# Polls the app for queued print jobs and routes them by document type:
#   delivery slips -> Konica (A4), box labels -> Deli 750W (105x70 mm).
#
# Setup:
#   1. Install SumatraPDF (https://www.sumatrapdfreader.org) and set $sumatra.
#   2. Set the exact Windows printer names below (Control Panel > Printers).
#   3. In the Deli 750W driver, set media to 70 x 105 mm portrait.
#   4. Set the EPIQ_PRINT_AGENT_TOKEN environment variable on the PC, or
#      enter the token at the hidden prompt when the script starts.
#   5. Run:  powershell -ExecutionPolicy Bypass -File print_agent.ps1
#
# The token must not be committed to this file or passed as a command-line
# argument. It is read from EPIQ_PRINT_AGENT_TOKEN or entered securely.
#
# Dev testing (optional, off by default):
#   To also poll development, pass:
#     powershell -ExecutionPolicy Bypass -File print_agent.ps1 -DevUrl "https://your-dev-domain.replit.dev"
#   To poll ONLY development, add -DevOnly:
#     powershell -ExecutionPolicy Bypass -File print_agent.ps1 -DevUrl "https://your-dev-domain.replit.dev" -DevOnly
#   Each printed job is logged with [PROD] or [DEV]. Restart without -DevUrl
#   to disable development polling.

param(
    [string]$DevUrl = "",
    [switch]$DevOnly
)

$baseUrl = "https://ep-picking-bro.replit.app"
$konica  = "KONICA MINOLTA 287SeriesPCL" # exact Windows printer name — A4 slips
$deli    = "EPIC_LABEL_PRINTER"          # exact Windows printer name — box labels
$sumatra = "C:\Program Files\SumatraPDF\SumatraPDF.exe"

# Prefer an environment variable so the token is not stored in this script.
# If it is absent, ask interactively without echoing it.
$token = $env:EPIQ_PRINT_AGENT_TOKEN
if ([string]::IsNullOrWhiteSpace($token)) {
    $secureToken = Read-Host "Enter EPIQ print-agent token" -AsSecureString
    $tokenPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    try {
        $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPtr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPtr)
    }
}
if ([string]::IsNullOrWhiteSpace($token)) {
    Write-Host "ERROR: No print-agent token supplied."
    exit 1
}

$headers = @{ "X-Print-Agent-Token" = $token }

# Build the list of poll targets: production first, then development (if enabled).
$targets = @()
if (-not $DevOnly) {
    $targets += @{ Env = "PROD"; Url = $baseUrl }
}
if (-not [string]::IsNullOrWhiteSpace($DevUrl)) {
    $targets += @{ Env = "DEV"; Url = $DevUrl.TrimEnd("/") }
} elseif ($DevOnly) {
    Write-Host "ERROR: -DevOnly requires -DevUrl."
    exit 1
}

Write-Host ("EPIQ print agent started. Polling: " +
    (($targets | ForEach-Object { "[{0}] {1}" -f $_.Env, $_.Url }) -join ", "))
while ($true) {
    $printed = $false
    foreach ($t in $targets) {
        try {
            $r = Invoke-RestMethod -Uri "$($t.Url)/print/agent/poll" -Headers $headers -Method Get -TimeoutSec 30
            if ($r.job) {
                $job = $r.job
                $tmp = Join-Path $env:TEMP ("epiq_{0}_{1}.pdf" -f $job.doc_type, $job.job_id)
                [IO.File]::WriteAllBytes($tmp, [Convert]::FromBase64String($job.pdf_base64))
                $printer = if ($job.doc_type -eq "label") { $deli } else { $konica }
                Write-Host ("[{0}] Job {1}: {2} for {3} -> {4}" -f $t.Env, $job.job_id, $job.doc_type, $job.invoice_no, $printer)
                if ($job.doc_type -eq "label") {
                    & $sumatra -print-to $printer -print-settings "noscale" -silent $tmp
                } else {
                    & $sumatra -print-to $printer -silent $tmp
                }
                $ok = ($LASTEXITCODE -eq 0)
                $body = @{ job_id = $job.job_id; ok = $ok } | ConvertTo-Json
                Invoke-RestMethod -Uri "$($t.Url)/print/agent/ack" -Headers $headers -Method Post -Body $body -ContentType "application/json" | Out-Null
                Remove-Item $tmp -ErrorAction SilentlyContinue
                $printed = $true
            }
        } catch {
            Write-Host ("[{0}] Poll error: {1}" -f $t.Env, $_)
        }
    }
    if (-not $printed) {
        Start-Sleep -Seconds 3
    }
    # When a job was printed, loop immediately to drain both environments.
}
