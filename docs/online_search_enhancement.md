# Online Search Enhancement Plan

## Current State
- **Analyze stage**: Computes hashes, fingerprints, BPM, key, energy from audio files
- **Enrich stage**: Fetches metadata from Last.fm and MusicBrainz using artist/title
- **Adapters**: LastFMAdapter, MusicBrainzAdapter

## What's Missing
The analyze stage computes a **Chromaprint fingerprint** but doesn't use it to search online databases for additional metadata.

## Proposed Enhancements

### 1. AcousticID Lookup (High Priority)
Use the Chromaprint fingerprint to find tracks in the AcousticID database.

**Benefits**:
- Find exact track matches even with different filenames
- Get MusicBrainz recording ID automatically
- Access to additional metadata from MusicBrainz
- Works even if artist/title are incorrect

**Implementation**:
- Create `AcousticIDAdapter` 
- Call AcousticID API with fingerprint during enrich stage
- Store matched recording ID and confidence

**API**: https://acoustid.org/webservice

### 2. Spotify Audio Features (High Priority)
Get detailed audio features for mood/energy analysis.

**Features**:
- `danceability` (0-1): How suitable for dancing
- `valence` (0-1): Musical positiveness (happy vs sad)
- `energy` (0-1): Intensity and activity
- `acousticness` (0-1): Acoustic vs electronic
- `instrumentalness` (0-1): Vocal vs instrumental
- `speechiness` (0-1): Spoken word content
- `liveness` (0-1): Live performance detection

**Benefits**:
- Better mood-based playlist creation
- Danceability scoring for DJ sets
- Vocal/instrumental detection

**Implementation**:
- Create `SpotifyAdapter`
- Requires Spotify API credentials (Client ID/Secret)
- Search by artist + title, or use ISRC if available
- Store audio features in new table

**API**: https://developer.spotify.com/documentation/web-api

### 3. Discogs Integration (Medium Priority)
Get release information for vinyl collectors.

**Data**:
- Label, catalog number
- Release year, country
- Format (vinyl, CD, digital)
- Genre, style tags

**Benefits**:
- Vinyl collection management
- Label-based playlists
- Era/decade accuracy

**Implementation**:
- Create `DiscogsAdapter`
- Requires Discogs API token
- Search by artist + title
- Store release info

**API**: https://www.discogs.com/developers

### 4. Beatport for Electronic Music (Medium Priority)
Specialized metadata for electronic/DJ music.

**Data**:
- Accurate BPM (often more precise)
- Musical key (Camelot notation)
- Genre/subgenre classification
- Release date, label

**Benefits**:
- Industry-standard BPM/key for DJs
- Better genre classification for electronic music
- Label-based playlists

**Implementation**:
- Create `BeatportAdapter`
- Web scraping or unofficial API
- Search by artist + title
- Store DJ-specific metadata

### 5. Lyrics from Genius (Low Priority)
Get song lyrics for sing-along playlists.

**Benefits**:
- Lyrics-based search
- Sing-along playlists
- Content analysis

**Implementation**:
- Create `GeniusAdapter`
- Requires Genius API token
- Search by artist + title
- Store lyrics text

**API**: https://docs.genius.com/

## Implementation Priority

### Phase 1: Core Enhancements (1-2 days)
1. **AcousticID Adapter** - Use fingerprints for better matching
2. **Spotify Adapter** - Audio features for mood analysis

### Phase 2: DJ-Specific (2-3 days)
3. **Beatport Adapter** - Electronic music metadata
4. **Discogs Adapter** - Release information

### Phase 3: Content (1-2 days)
5. **Genius Adapter** - Lyrics

## Database Schema Changes

### New table: `audio_features`
```sql
CREATE TABLE audio_features (
    metadata_id TEXT PRIMARY KEY,
    source TEXT, -- 'spotify', 'acousticid', 'beatport'
    danceability REAL, -- 0-1
    valence REAL, -- 0-1 (sad to happy)
    energy REAL, -- 0-1
    acousticness REAL, -- 0-1
    instrumentalness REAL, -- 0-1
    speechiness REAL, -- 0-1
    liveness REAL, -- 0-1
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (metadata_id) REFERENCES tracks(metadata_id)
);
```

### New table: `release_info`
```sql
CREATE TABLE release_info (
    metadata_id TEXT PRIMARY KEY,
    source TEXT, -- 'discogs', 'beatport'
    label TEXT,
    catalog_number TEXT,
    release_year INTEGER,
    release_country TEXT,
    format TEXT, -- 'vinyl', 'cd', 'digital'
    genre TEXT,
    style TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (metadata_id) REFERENCES tracks(metadata_id)
);
```

## Configuration

### Required API Keys (in .env)
```
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
ACOUSTID_API_KEY=
DISCOGS_TOKEN=
GENIUS_ACCESS_TOKEN=
```

## Workflow Integration

### During Enrich Stage
1. Try AcousticID lookup (if fingerprint available)
2. Try Spotify search (for audio features)
3. Try Beatport search (if electronic genre detected)
4. Try Discogs search (for release info)
5. Try Genius search (for lyrics)

### Fallback Strategy
- If AcousticID fails, fall back to Last.fm/MusicBrainz
- If Spotify fails, compute basic features from audio analysis
- Cache all results to avoid repeated API calls

## Benefits for DJ Playlists

1. **Better Matching**: AcousticID finds tracks even with wrong filenames
2. **Mood Playlists**: Spotify valence/energy for vibe-based sets
3. **Danceability**: Spotify danceability for club playlists
4. **Vocal Detection**: Instrumentalness for background music
5. **Genre Accuracy**: Beatport/Discogs for proper classification
6. **Release Info**: Label/vinyl info for collector sets