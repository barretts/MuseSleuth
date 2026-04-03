# DJ Playlist Enrichment Features - Implementation Plan

## Overview
Add creative enrichment features to help with DJ playlist creation.

## Features to Implement

### 1. Tempo Categories (Easy - Derive from existing BPM)
- **Source**: Existing BPM data in `musical_features` table
- **Categories**:
  - Chill: < 100 BPM
  - Medium: 100-128 BPM  
  - High Energy: 128+ BPM
- **Implementation**: Add `tempo_category` column to `musical_features` or create new `playlist_signals` field
- **Effort**: Low - just derive from existing data

### 2. Mood/Emotion Analysis (Medium - Requires external API or audio analysis)
- **Option A**: Spotify API (requires API key)
  - Valence: 0-1 (sad to happy)
  - Energy: 0-1 (calm to energetic)
  - Danceability: 0-1
- **Option B**: Acoustic analysis libraries (librosa, essentia)
  - Analyze audio files directly
  - No API dependency
- **Implementation**: 
  - Add `mood_valence`, `mood_energy` columns
  - Create new adapter (SpotifyAdapter or AcousticAdapter)
- **Effort**: Medium - needs new adapter

### 3. Danceability Score (Medium - Requires external API or audio analysis)
- **Option A**: Spotify API provides danceability score 0-100
- **Option B**: Compute from audio features:
  - Rhythm stability
  - Beat strength
  - Tempo regularity
- **Implementation**:
  - Add `danceability_score` column to `musical_features`
  - Add to enrich_runner
- **Effort**: Medium

### 4. Transition Compatibility Score (High - Complex algorithm)
- **Based on existing data**:
  - Key compatibility (Camelot wheel distance)
  - BPM similarity (difference threshold)
  - Energy flow (energy level progression)
- **Algorithm**:
  - Score = (key_compatibility * 0.4) + (bpm_similarity * 0.3) + (energy_flow * 0.3)
  - Key compatibility: Adjacent keys on Camelot wheel = 100%, 2 steps = 75%, etc.
  - BPM similarity: < 5 BPM diff = 100%, < 10 = 75%, etc.
- **Implementation**:
  - Create new `track_compatibility` table
  - Pre-compute compatibility scores for track pairs
  - Or compute on-demand in web UI
- **Effort**: High - complex algorithm, many track pairs

## Implementation Priority

### Phase 1: Quick Wins (1-2 hours)
1. ✅ **Tempo Categories** - Add to `playlist_signals` table
2. ✅ **Energy Tiers** - Already have energy, just need tier labels

### Phase 2: Medium Features (4-8 hours)
3. **Mood/Emotion Analysis** - Spotify API integration
4. **Danceability Score** - From Spotify or computed

### Phase 3: Advanced Features (1-2 days)
5. **Transition Compatibility** - Pre-computed scores

## Database Schema Changes

### New columns in `playlist_signals`:
```sql
ALTER TABLE playlist_signals ADD COLUMN tempo_category TEXT; -- 'chill', 'medium', 'high_energy'
ALTER TABLE playlist_signals ADD COLUMN mood_valence REAL; -- 0-1
ALTER TABLE playlist_signals ADD COLUMN mood_energy REAL; -- 0-1
ALTER TABLE playlist_signals ADD COLUMN danceability REAL; -- 0-100
```

### New table for compatibility:
```sql
CREATE TABLE track_compatibility (
    metadata_id_1 TEXT,
    metadata_id_2 TEXT,
    compatibility_score REAL, -- 0-100
    key_score REAL,
    bpm_score REAL,
    energy_score REAL,
    PRIMARY KEY (metadata_id_1, metadata_id_2)
);
```

## Next Steps
1. Implement Phase 1 (tempo categories)
2. Set up Spotify API credentials
3. Implement Spotify adapter for mood/danceability
4. Build compatibility scoring algorithm