<#
.SYNOPSIS
    Invoke drive mapping functionality from anywhere

.DESCRIPTION
    This script provides a simple, memorable way to map network drives
    based on the output of 'net use <drive>:'. It's designed to be
    dot-sourced into your PowerShell profile for global access.

.PARAMETER DriveLetter
    The drive letter to map (e.g., "Z:")

.EXAMPLE
    # After running net use Z: and seeing the network path in output
    Invoke-DriveMap -DriveLetter Z:

.EXAMPLE
    # Alias for even quicker use
    idm Z:

.NOTES
    To make this available everywhere:
    1. Copy this script to a permanent location
    2. Add this line to your PowerShell profile (notepad $PROFILE):
       . "C:\path\to\Invoke-DriveMap.ps1"
    3. Restart PowerShell
    4. Use: Invoke-DriveMap -DriveLetter Z:  or  idm Z:
#>

function Invoke-DriveMap {
    [CmdletBinding()]
    param (
        [Parameter(Mandatory=$true, Position=0)]
        [ValidatePattern('^[a-zA-Z]:$')]
        [string]$DriveLetter
    )

    # Normalize drive letter
    $DriveLetter = $DriveLetter.ToUpper().TrimEnd(':') + ':'

    Write-Host "Getting network path for $DriveLetter from 'net use' output..." -ForegroundColor Cyan

    # Run net use and parse output
    try {
        $netUseOutput = net use 2>&1
        $lastExitCode = $LASTEXITCODE

        if ($lastExitCode -ne 0) {
            Write-Warning "Failed to run 'net use': $netUseOutput"
            return $false
        }

        # Look for line containing our drive letter
        $driveLine = $netUseOutput | Where-Object { $_ -like "*$DriveLetter*" } | Select-Object -First 1

        if (-not $driveLine) {
            Write-Warning "Drive letter $DriveLetter not found in 'net use' output."
            Write-Host "Available connections:" -ForegroundColor Yellow
            $netUseOutput | Where-Object { $_ -like "*[a-zA-Z]:" } | ForEach-Object { Write-Host "  $_" }
            return $false
        }

        # Extract the network path
        $parts = $driveLine -split '\s+'
        if ($parts.Length -ge 3) {
            $networkPath = $parts[2]
        } else {
            # Fallback extraction
            $networkPath = $driveLine -replace [regex]::Escape($DriveLetter.TrimEnd(':')), '' -replace '^\s+|\s+$', ''
            $networkPath = $networkPath -split '\s+' | Select-Object -First 1
            if (-not $networkPath) { $networkPath = $driveLine.Trim() }
        }

        if (-not $networkPath -or $networkPath -eq '') {
            Write-Warning "Could not extract network path from line: '$driveLine'"
            return $false
        }

        Write-Host "Found network path: $networkPath" -ForegroundColor Green

        # Check if already mapped and accessible
        if (Test-Path "$DriveLetter\") {
            Write-Host "Drive $DriveLetter is already accessible." -ForegroundColor Green
            return $true
        }

        # Map the drive
        Write-Host "Mapping $DriveLetter to $networkPath..." -ForegroundColor Cyan
        $mapResult = net use $DriveLetter $networkPath /persistent:yes 2>&1
        $mapExitCode = $LASTEXITCODE

        if ($mapExitCode -eq 0) {
            Write-Host "Successfully mapped $DriveLetter to $networkPath" -ForegroundColor Green

            # Verify
            if (Test-Path "$DriveLetter\") {
                Write-Host "Verified: $DriveLetter is now accessible." -ForegroundColor Green
                return $true
            } else {
                Write-Warning "Mapping reported success but drive not accessible."
                return $false
            }
        } else {
            if ($mapResult -like "*already connected*") {
                Write-Warning "The network path is already connected using different credentials."
                Write-Host "Try: net use $DriveLetter /delete then run this command again." -ForegroundColor Yellow
            } else {
                Write-Warning "Failed to map drive:`n$mapResult"
            }
            return $false
        }
    } catch {
        Write-Error "Exception occurred: $_"
        return $false
    }
}

# Create alias for even quicker usage
Set-Alias -Name idm -Value Invoke-DriveMap -Option AllScope -Description "Invoke-DriveMap alias"

# Export the function and alias if this is being dot-sourced
if ($MyInvocation.InvocationName -eq '.\\Invoke-DriveMap.ps1') {
    Export-ModuleMember -Function Invoke-DriveMap -Alias idm
}