param(
    [Parameter(Mandatory=$true)][string]$Title,
    [Parameter(Mandatory=$true)][string]$Message
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notice = New-Object System.Windows.Forms.NotifyIcon
try {
    $notice.Icon = [System.Drawing.SystemIcons]::Information
    $notice.Text = 'QQCompanion'
    $notice.BalloonTipTitle = $Title
    $notice.BalloonTipText = $Message
    $notice.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
    $notice.add_BalloonTipClicked({
        Start-Process 'http://127.0.0.1:8765/startup'
    })
    $notice.Visible = $true
    $notice.ShowBalloonTip(15000)
    $until = [DateTime]::UtcNow.AddSeconds(20)
    while ([DateTime]::UtcNow -lt $until) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 100
    }
    Write-Output ('Notification submitted: ' + $Title)
} finally {
    $notice.Visible = $false
    $notice.Dispose()
}
