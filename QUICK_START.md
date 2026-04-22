# Quick Start: Global Drive Mapping Command

## The Problem
You can see network drives in File Explorer but not in PowerShell due to context/elevation differences.

## The Solution: Invoke-DriveMap Command

I've created a simple, memorable PowerShell command that you can use **from anywhere** once installed.

### Installation (One-Time Setup)

**Option 1: Install for Current User Only (Recommended)**
```powershell
cd C:\code\MuseSleuth
.\Install-DriveMapCommand.ps1 -Scope CurrentUser
```

**Option 2: Install for All Users (Requires Admin)**
```powershell
cd C:\code\MuseSleuth
.\Install-DriveMapCommand.ps1 -Scope AllUsers
```

### Usage (After Installation)

Once installed, you can use this **from any PowerShell prompt, anywhere**:

```powershell
# Step 1: Check what's remembered for Z: (you already do this)
net use Z:

# Step 2: Map it using the simple command
Invoke-DriveMap -DriveLetter Z:

# Step 3: Or use the even shorter alias
idm Z:

# Step 4: Verify it worked
ls Z:\
```

### Examples

```powershell
# After seeing this from net use Z:
# Status       Local     Remote                    Network
# OK           Z:        \fileserver\music        Microsoft Windows Network

# Just run:
idm Z:

# Works with any drive letter
idm M:
idm X:
```

### How It Works

1. Runs `net use Z:` behind the scenes
2. Extracts the network path from the output
3. Maps the drive using that path with `/persistent:yes`
4. Verifies the mapping worked
5. Returns success/failure status

### Where It's Installed

The command is added to your PowerShell profile:
- Current User: `$PROFILE.CurrentUserAllHosts`
- All Users: `$PROFILE.AllUsersAllHosts`

To manually add it instead, add this line to your profile:
```powershell
. "C:\code\MuseSleuth\Invoke-DriveMap.ps1"
```

### Uninstallation

To remove, simply delete the line `. "C:\code\MuseSleuth\Invoke-DriveMap.ps1"` from your PowerShell profile.

---

**Remember**: This must be run in **Windows PowerShell**, not Git Bash/Cygwin/WSL.
