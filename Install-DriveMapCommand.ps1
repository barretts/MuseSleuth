<#
.SYNOPSIS
    Install the Invoke-DriveMap command globally

.DESCRIPTION
    This script helps you install the Invoke-DriveMap functionality
    so it's available from any PowerShell prompt, anywhere.

.EXAMPLE
    .\Install-DriveMapCommand.ps1 -Scope CurrentUser
    .\Install-DriveMapCommand.ps1 -Scope AllUsers

.PARAMETER Scope
    Installation scope: CurrentUser or AllUsers (default: CurrentUser)
#>

[CmdletBinding()]
param (
    [ValidateSet('CurrentUser', 'AllUsers')]
    [string]$Scope = 'CurrentUser'
)

# Determine profile path based on scope
if ($Scope -eq 'AllUsers') {
    $profilePath = $PROFILE.AllUsersAllHosts
} else {
    $profilePath = $PROFILE.CurrentUserAllHosts
}

# Ensure the profile directory exists
$profileDir = Split-Path $profilePath
if (-not (Test-Path $profileDir)) {
    Write-Host "Creating profile directory: $profileDir" -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
}

# Path to our Invoke-DriveMap script
$scriptPath = "C:\code\MuseSleuth\Invoke-DriveMap.ps1"

# Check if script exists
if (-not (Test-Path $scriptPath)) {
    Write-Error "Invoke-DriveMap.ps1 not found at: $scriptPath"
    Write-Host "Please ensure you're running this from the MuseSleuth project directory." -ForegroundColor Red
    exit 1
}

# Check if already imported in profile
$importLine = ". `"$scriptPath`""
if (Test-Path $profilePath) {
    $profileContent = Get-Content $profilePath -Raw
    if ($profileContent -contains $importLine) {
        Write-Host "Invoke-DriveMap is already installed in your $Scope profile." -ForegroundColor Green
        Write-Host "Profile: $profilePath"
        return
    }
}

# Add import to profile
Write-Host "Adding Invoke-DriveMap to your $Scope PowerShell profile..." -ForegroundColor Cyan
Add-Content -Path $profilePath -Value ""
Add-Content -Path $profilePath -Value "# Invoke-DriveMap - Network drive mapping utility"
Add-Content -Path $profilePath -Value $importLine
Add-Content -Path $profilePath -Value ""

Write-Host "Successfully installed Invoke-DriveMap!" -ForegroundColor Green
Write-Host ""
Write-Host "To use it:"
Write-Host "  1. Restart your PowerShell prompt"
Write-Host "  2. Run: net use Z: (to see if Z: is remembered)"
Write-Host "  3. Run: Invoke-DriveMap -DriveLetter Z:"
Write-Host "  4. Or use the alias: idm Z:"
Write-Host ""
Write-Host "Profile updated: $profilePath"