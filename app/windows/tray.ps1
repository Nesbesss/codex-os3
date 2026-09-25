# os3-router tray icon for Windows (beta). Polls the router's local API and
# offers the same actions as the macOS menu bar app. Started at logon by install.ps1.
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

$HomeDir = if ($env:CODEX_OS3_HOME) { $env:CODEX_OS3_HOME } else { Join-Path $env:USERPROFILE ".codex-os3" }
function Port {  # config.json only holds changed settings: no "port" key means the default
    try { $p = (Get-Content (Join-Path $HomeDir "config.json") -Raw | ConvertFrom-Json).port } catch { $p = $null }
    if ($p) { $p } else { 11435 }
}
function Base { "http://127.0.0.1:$(Port)" }
function Api($path) { Invoke-RestMethod "$(Base)/api/$path" -TimeoutSec 5 }
function Post($path) {
    Invoke-RestMethod "$(Base)/api/$path" -Method Post -Body "{}" -ContentType "application/json" `
        -Headers @{ "X-Codex-OS3" = "1" } -TimeoutSec 120
}

$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Visible = $true
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$statusItem = $menu.Items.Add("os3-router: …"); $statusItem.Enabled = $false
$limitItem = $menu.Items.Add("limits: …"); $limitItem.Enabled = $false
$menu.Items.Add("-") | Out-Null
$menu.Items.Add("Open dashboard", $null, { Start-Process "http://localhost:$(Port)/" }) | Out-Null
$menu.Items.Add("Copy OS3 settings", $null, {
    $c = Api "config"
    [System.Windows.Forms.Clipboard]::SetText("endpoint: http://localhost:$($c.port)/v1`nmodel id: $($c.model)`napi key: $($c.api_key)`ncontext window: 200000")
    $icon.ShowBalloonTip(2000, "os3-router", "OS3 settings copied", "Info")
}) | Out-Null
$menu.Items.Add("Copy API key", $null, { [System.Windows.Forms.Clipboard]::SetText((Api "config").api_key) }) | Out-Null
$menu.Items.Add("Restart rabbit-agent", $null, {
    try { $r = Post "agent/restart"; $icon.ShowBalloonTip(3000, "os3-router", $r.message, "Info") } catch {}
}) | Out-Null
$menu.Items.Add("Reload router", $null, { try { Post "reload" | Out-Null } catch {} }) | Out-Null
$menu.Items.Add("-") | Out-Null
$menu.Items.Add("Quit tray", $null, { $icon.Visible = $false; [System.Windows.Forms.Application]::Exit() }) | Out-Null
$icon.ContextMenuStrip = $menu
$icon.add_DoubleClick({ Start-Process "http://localhost:$(Port)/" })

function Dot($color) {
    $bmp = New-Object System.Drawing.Bitmap 16, 16
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = "AntiAlias"
    $g.FillEllipse((New-Object System.Drawing.SolidBrush $color), 2, 2, 12, 12)
    $g.Dispose()
    [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
}
$green = Dot ([System.Drawing.Color]::FromArgb(12, 163, 12))
$amber = Dot ([System.Drawing.Color]::FromArgb(250, 178, 25))
$red = Dot ([System.Drawing.Color]::FromArgb(208, 59, 59))

function Plain($body) {  # changelog markdown -> plain bullets
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($l in ($body -split "`n")) {
        if ($l -match '^\s*- ') { $out.Add("• " + ($l -replace '^\s*- ', '')) }
        elseif ($l.Trim() -and $out.Count) { $out[$out.Count - 1] += " " + $l.Trim() }
    }
    (($out -join "`n") -replace '\*\*', '') -replace '`', ''
}
$script:wnShown = $false
$script:lastAlert = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()  # only alerts from now on
function WhatsNew {  # after an update, once (the web UI or menu bar app may show it instead)
    $script:wnShown = $true
    try {
        $w = Api "whatsnew"
        if (-not $w.show) { return }
        $text = ($w.sections | ForEach-Object { $_.title + "`n" + (Plain $_.body) }) -join "`n`n"
        [System.Windows.Forms.MessageBox]::Show("os3-router was updated.`n`n$text", "What's new in os3-router $($w.version)") | Out-Null
        Post "whatsnew/seen" | Out-Null
    } catch {}
}

function Update {
    try {
        $s = Api "status"
        $errs = @($s.watchdog.findings | Where-Object { $_.level -eq "error" })
        $agentOk = $s.agent.status -eq "connected" -and $s.agent.running
        $icon.Icon = if (-not $agentOk -or $errs.Count) { $red } elseif ($s.limits.s_pct -ge 90) { $amber } else { $green }
        $statusItem.Text = if ($agentOk) { "rabbit-agent connected · $($s.model)" } else { "rabbit-agent: $($s.agent.status)" }
        $limitItem.Text = "5h: $([math]::Round($s.limits.p_pct))%  ·  weekly: $([math]::Round($s.limits.s_pct))%"
        $icon.Text = "os3-router — $($statusItem.Text)".Substring(0, [Math]::Min(63, "os3-router — $($statusItem.Text)".Length))
        if ($s.whats_new -and -not $script:wnShown) { WhatsNew }
        foreach ($a in @($s.alerts | Where-Object { $_.ts -gt $script:lastAlert })) {  # 90% limit, fallback switch
            $icon.ShowBalloonTip(8000, "os3-router", $a.text, "Warning"); $script:lastAlert = $a.ts
        }
    } catch {
        $icon.Icon = $red; $statusItem.Text = "router not running"; $icon.Text = "os3-router — router not running"
    }
}
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 10000
$timer.add_Tick({ Update })
$timer.Start()
Update
[System.Windows.Forms.Application]::Run()
