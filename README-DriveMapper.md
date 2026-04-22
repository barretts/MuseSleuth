# Drive Mapper Utility for PowerShell

This utility solves the common Windows issue where network drives mapped in File Explorer are not visible in PowerShell due to user context/elevation differences.

## Files Created

1. **Map-NetworkDrive.ps1** - Standalone script to map a network drive with specified letter and path
2. **QuickDriveMap.ps1** - Script designed specifically for your use case: runs after you've seen `net use Z:` output and automatically maps the drive
3. **DriveMapper.psm1** + **DriveMapper.psd1** - PowerShell module version for reusable functionality

## How to Use QuickDriveMap.ps1 (Recommended for your situation)

Based on your statement: "when I ran net use Z: it gave me all the info to do the next step"

### Step-by-Step:
1. Run `net use Z:` in PowerShell (you already did this)
2. Note the network path shown in the output (it should look like `\\server\share`)
3. Run: `.\QuickDriveMap.ps1 -DriveLetter Z:`
4. The script will:
   - Parse the output from `net use Z:` to extract the network path
   - Map the drive using that path with `/persistent:yes`
   - Verify the mapping worked

### Example:
```powershell
PS C:\> net use Z:
New connections will be remembered.

Status       Local     Remote                    Network
-------------------------------------------------------------------------------
OK           Z:        \\fileserver\music        Microsoft Windows Network
The command completed successfully.

PS C:\> .\QuickDriveMap.ps1 -DriveLetter Z:
Getting network path for Z: from 'net use' output...
Found network path: \\fileserver\music
Mapping Z: to \\fileserver\music...
Successfully mapped Z: to \\fileserver\music
Verified: Z: is now accessible.
```

## Alternative: Manual Mapping with Map-NetworkDrive.ps1

If you know the network path:
```powershell
.\Map-NetworkDrive.ps1 -DriveLetter Z: -NetworkPath "\\fileserver\music"
```

## Making It Permanent

To automatically map drives in all PowerShell sessions, add this to your PowerShell profile (`notepad $PROFILE`):

```powershell
# Auto-map network drives when needed
if (-not (Test-Path "Z:\")) {
    . "C:\code\MuseSleuth\Map-NetworkDrive.ps1"
    Map-NetworkDrive -DriveLetter "Z:" -NetworkPath "\\fileserver\music"
}
```

Replace `\\fileserver\music` with your actual network path.

## Troubleshooting

If you get "already connected using different credentials" errors:
```powershell
net use Z: /delete
.\QuickDriveMap.ps1 -DriveLetter Z:
```

## Requirements

- PowerShell 3.0 or later
- Run from the same context (admin/non-admin) where you need the drive