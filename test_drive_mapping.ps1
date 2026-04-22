# Test script to demonstrate drive mapping functionality
Write-Host "=== Drive Mapper Test Script ===" -ForegroundColor Cyan
Write-Host ""

# Show current mappings
Write-Host "Current network mappings:" -ForegroundColor Yellow
net use | Where-Object { $_ -match '^[A-Z]:' } | ForEach-Object { Write-Host "  $_" }
Write-Host ""

# Show what QuickDriveMap would do
Write-Host "To test QuickDriveMap.ps1:" -ForegroundColor Green
Write-Host "1. First run: net use Z: (to see if Z: is remembered)"
Write-Host "2. If it shows a network path, then run: .\QuickDriveMap.ps1 -DriveLetter Z:"
Write-Host ""
Write-Host "To test Map-NetworkDrive.ps1 manually:" -ForegroundColor Green
Write-Host ".\Map-NetworkDrive.ps1 -DriveLetter Z: -NetworkPath \"\\your-server\\your-share\""
Write-Host ""
Write-Host "=== End Test Instructions ===" -ForegroundColor Cyan
