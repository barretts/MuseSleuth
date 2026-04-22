<#
.SYNOPSIS
    Quick network drive mapper - designed to be run after you've seen the output of 'net use Z:'

.DESCRIPTION
    This script is meant to be used when you've already run 'net use Z:' and saw the
    network path in the output. It extracts that path and maps the drive for you.

    Based on your statement: "when I ran net use Z: it gave me all the info to do the next step"

.NOTES
    Author: Created for MuseSleuth project
#>

function QuickMapDrive {
    param (
        [Parameter(Mandatory=$true)]
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
            $netUseOutput | Where-Object { $_ -like "*[a-zA-Z]:*" } | ForEach-Object { Write-Host "  $_" }
            return $false
        }

        # Extract the network path (typically second or third column)
        # Format:  Drive Letter       Connection Type      Network Path                Status
        $parts = $driveLine -split '\s+'
        if ($parts.Length -ge 3) {
            $networkPath = $parts[2]
        } else {
            # Fallback: try to extract after the drive letter
            $networkPath = $driveLine -replace [regex]::Escape($DriveLetter.TrimEnd(':')), '' -replace '^\s+|\s+$', ''
            $networkPath = $networkPath -split '\s+|(?<=:)\\|(?<=\\)\$' | Select-Object -First 1
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
                Write-Host "Try: net use $DriveLetter /delete then run this script again." -ForegroundColor Yellow
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

# If invoked directly
if ($PSInvokeCommand.CommandType -eq 'Application' -or $MyInvocation.InvocationName -eq '.\\QuickDriveMap.ps1') {
    Write-Host @"
QuickDriveMap.ps1 - Automatically map a drive based on 'net use' output

Usage:
    .\QuickDriveMap.ps1 -DriveLetter Z:

This script will:
1. Run 'net use' to see current connections
2. Find the line for your specified drive letter
3. Extract the network path from that line
4. Map the drive using that path (with /persistent:yes)

Example:
    .\QuickDriveMap.ps1 -DriveLetter Z:
"@
}