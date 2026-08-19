# EPIQ print bridge — runs on the office PC.
# Polls the app for queued print jobs and routes them by document type:
#   delivery slips -> Konica (A4), box labels -> Deli 750W (105x70 mm).
#
# Setup:
#   1. Install SumatraPDF (https://www.sumatrapdfreader.org) and set $sumatra.
#   2. Set the exact Windows printer names below (Control Panel > Printers).
#   3. In the Deli 750W driver, set default paper size to 105 x 70 mm landscape.
#   4. Set $baseUrl to the app URL and $token to the PRINT_AGENT_TOKEN secret.
#   5. Run:  powershell -ExecutionPolicy Bypass -File print_agent.ps1
#
# Dev testing (optional, off by default):
#   To also poll the development environment (so label tweaks can be tested
#   without publishing), pass the dev URL with -DevUrl:
#     powershell -ExecutionPolicy Bypass -File print_agent.ps1 -DevUrl "https://<your-repl>.replit.dev"
#   To poll ONLY dev (skip production), add -DevOnly:
#     powershell -ExecutionPolicy Bypass -File print_agent.ps1 -DevUrl "https://<your-repl>.replit.dev" -DevOnly
#   The same X-Print-Agent-Token works in both environments. Each printed job
#   is logged with the environment ([PROD] / [DEV]) it came from.
#   To disable dev polling again, just restart the agent without -DevUrl.

param(
    [string]$DevUrl = "",
    [switch]$DevOnly
)

$baseUrl = "https://ep-picking-bro.replit.app"
$token   = "PASTE_PRINT_AGENT_TOKEN_HERE"
$konica  = "KONICA MINOLTA C300i"     # exact Windows printer name — A4 slips
$deli    = "Deli DL-750W"             # exact Windows printer name — box labels
$sumatra = "C:\Program Files\SumatraPDF\SumatraPDF.exe"

$headers = @{ "X-Print-Agent-Token" = $token }

# Build the list of poll targets: production first, then dev (if enabled).
$targets = @()
if (-not $DevOnly) {
    $targets += @{ Env = "PROD"; Url = $baseUrl }
}
if ($DevUrl -ne "") {
    $targets += @{ Env = "DEV"; Url = $DevUrl.TrimEnd("/") }
} elseif ($DevOnly) {
    Write-Host "ERROR: -DevOnly requires -DevUrl."
    exit 1
}

Write-Host ("EPIQ print agent started. Polling: " + (($targets | ForEach-Object { "[{0}] {1}" -f $_.Env, $_.Url }) -join ", "))
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
                & $sumatra -print-to $printer -silent $tmp
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
    # if a job was printed, loop immediately to check all targets again
}
