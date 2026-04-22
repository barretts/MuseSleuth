# Continue Query

Query y:\music.db and z:\music_new.db (network mapped drives) and list out all songs that are NOT album release tracks - no demos, no remixes, nothing live, no covers (no kids bop).

**Keep:** official tracks, remasters, vinyl releases (these are the real track)

**Exclude:**
- Demos
- Remixes
- Live tracks
- Covers (including Kids Bop)

**Output:** CSV file with metadata and file paths.

The user wants to save the filtered results to a CSV file.

**Note:** I couldn't access the network drives Y: and Z: - they need to be available mapped drives or you'll need to run this query locally with access to those drives.