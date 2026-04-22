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

Export-ModuleMember -Function Map-NetworkDrive, QuickMapDrive