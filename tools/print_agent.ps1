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

$baseUrl = "https://ep-picking-bro.replit.app"
$token   = "PASTE_PRINT_AGENT_TOKEN_HERE"
$konica  = "KONICA MINOLTA C300i"     # exact Windows printer name — A4 slips
$deli    = "Deli DL-750W"             # exact Windows printer name — box labels
$sumatra = "C:\Program Files\SumatraPDF\SumatraPDF.exe"

$headers = @{ "X-Print-Agent-Token" = $token }

Write-Host "EPIQ print agent started. Polling $baseUrl ..."
while ($true) {
    try {
        $r = Invoke-RestMethod -Uri "$baseUrl/print/agent/poll" -Headers $headers -Method Get -TimeoutSec 30
        if ($r.job) {
            $job = $r.job
            $tmp = Join-Path $env:TEMP ("epiq_{0}_{1}.pdf" -f $job.doc_type, $job.job_id)
            [IO.File]::WriteAllBytes($tmp, [Convert]::FromBase64String($job.pdf_base64))
            $printer = if ($job.doc_type -eq "label") { $deli } else { $konica }
            Write-Host ("Job {0}: {1} for {2} -> {3}" -f $job.job_id, $job.doc_type, $job.invoice_no, $printer)
            & $sumatra -print-to $printer -silent $tmp
            $ok = ($LASTEXITCODE -eq 0)
            $body = @{ job_id = $job.job_id; ok = $ok } | ConvertTo-Json
            Invoke-RestMethod -Uri "$baseUrl/print/agent/ack" -Headers $headers -Method Post -Body $body -ContentType "application/json" | Out-Null
            Remove-Item $tmp -ErrorAction SilentlyContinue
            continue   # check immediately for the next job
        }
    } catch {
        Write-Host "Poll error: $_"
    }
    Start-Sleep -Seconds 3
}
