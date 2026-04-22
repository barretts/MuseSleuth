<#
.SYNOPSIS
    Maps a network drive if not already mapped in the current context.

.DESCRIPTION
    This script addresses the common issue where network drives mapped in File Explorer
    are not visible in PowerShell due to user context/elevation differences.
    It checks if a drive is already mapped, and if not, maps it persistently.

.PARAMETER DriveLetter
    The drive letter to map/check (e.g., "Z:")

.PARAMETER NetworkPath
    The UNC path to map to (e.g., "\\server\share")

.PARAMETER Persistent
    Switch to make the mapping persistent across reboots (default: $true)

.EXAMPLE
    Map-NetworkDrive -DriveLetter "Z:" -NetworkPath "\\fileserver\music"

.EXAMPLE
    Map-NetworkDrive -DriveLetter "M:" -NetworkPath "\\backup\archive" -Persistent:$false

.NOTES
    Author: Created for MuseSleuth project
    Requires: PowerShell 3.0 or later
#>

function Map-NetworkDrive {
    [CmdletBinding()]
    param (
        [Parameter(Mandatory=$true)]
        [ValidatePattern('^[a-zA-Z]:$')]
        [string]$DriveLetter,

        [Parameter(Mandatory=$true)]
        [string]$NetworkPath,

        [bool]$Persistent = $true
    )

    # Normalize drive letter to uppercase and ensure colon
    $DriveLetter = $DriveLetter.ToUpper().TrimEnd(':') + ':'

    Write-Verbose "Checking if $DriveLetter is already mapped..."

    # Check if drive is already accessible
    if (Test-Path "$DriveLetter\") {
        try {
            $root = (Get-Item "$DriveLetter\").Root
            if ($root -eq "$DriveLetter\") {
                Write-Host "Drive $DriveLetter is already accessible." -ForegroundColor Green
                return $true
            }
        } catch {
            # Drive exists but not accessible - continue to remap
            Write-Verbose "Drive $DriveLetter exists but is not accessible."
        }
    }

    # Check if drive letter is in use by something else
    $psDrive = Get-PSDrive -Name $DriveLetter.TrimEnd(':') -ErrorAction SilentlyContinue
    if ($psDrive) {
        Write-Warning "Drive letter $DriveLetter is already in use by a PS drive ($($psDrive.DisplayRoot))."
        Write-Host "Consider using a different drive letter or removing the existing mapping." -ForegroundColor Yellow
        return $false
    }

    # Build net use command
    $netUseArgs = @($NetworkPath)
    if ($Persistent) {
        $netUseArgs += "/persistent:yes"
    }

    Write-Host "Mapping $DriveLetter to $NetworkPath..." -ForegroundColor Cyan

    try {
        # Execute net use and capture output
        $result = net use $DriveLetter $NetworkPath @(
            if ($Persistent) { "/persistent:yes" }
        ) 2>&1

        $exitCode = $LASTEXITCODE

        if ($exitCode -eq 0) {
            Write-Host "Successfully mapped $DriveLetter to $NetworkPath" -ForegroundColor Green

            # Verify the mapping worked
            if (Test-Path "$DriveLetter\") {
                Write-Host "Verified: $DriveLetter is now accessible." -ForegroundColor Green
                return $true
            } else {
                Write-Warning "Mapping reported success but drive is not accessible."
                return $false
            }
        } else {
            # Check for specific error messages
            if ($result -like "*already connected*") {
                Write-Warning "The network path is already connected using a different user name or password."
                Write-Host "To connect with different credentials, first disconnect the existing connection." -ForegroundColor Yellow
            } elseif ($result -like "*access is denied*") {
                Write-Warning "Access denied. Check your permissions and credentials."
            } elseif ($result -like "*network path was not found*") {
                Write-Warning "Network path not found: $NetworkPath"
                Write-Host "Verify the server name, share name, and network connectivity." -ForegroundColor Yellow
            } else {
                Write-Warning "Failed to map drive. net use output:`n$result"
            }
            return $false
        }
    } catch {
        Write-Error "Exception occurred while mapping drive: $_"
        return $false
    }
}

# If script is invoked directly (not dot-sourced), provide usage help
if ($PSInvokeCommand.CommandType -eq 'Application' -or $MyInvocation.InvocationName -eq '.\\Map-NetworkDrive.ps1') {
    Write-Host @"
Map-NetworkDrive.ps1 - Utility to map network drives in PowerShell context

Usage:
    .\Map-NetworkDrive.ps1 -DriveLetter Z: -NetworkPath "\\server\share"
    .\Map-NetworkDrive.ps1 -DriveLetter M: -NetworkPath "\\backup\archive" -Persistent:$false

Examples:
    .\Map-NetworkDrive.ps1 -DriveLetter Z: -NetworkPath "\\fileserver\music"
    .\Map-NetworkDrive.ps1 -DriveLetter M: -NetworkPath "\\nas\backups" -Persistent:$true

To make this available in all PowerShell sessions, add this to your profile ($PROFILE):
    . "C:\path\to\Map-NetworkDrive.ps1"
"@
}