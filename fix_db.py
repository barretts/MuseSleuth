import sys
sys.path.insert(0, 'src')
from musesleuth.db import get_connection

conn = get_connection('music_new.db')

conn.execute('''
CREATE TABLE IF NOT EXISTS ml_features (
    metadata_id             TEXT PRIMARY KEY,
    genre_primary           TEXT,
    genre_secondary         TEXT,
    genre_confidence        REAL,
    mood_tags               TEXT,
    danceability            REAL,
    acousticness            REAL,
    electronic_score        REAL,
    vocal_type              TEXT,
    vocal_confidence        REAL,
    era_prediction          TEXT,
    era_confidence          REAL,
    tempo_category          TEXT,
    energy_category         TEXT,
    quality_score           REAL,
    quality_issues          TEXT,
    classified_at           TEXT
)''')
conn.commit()

# Check counts
mf = conn.execute('SELECT COUNT(*) FROM musical_features').fetchone()[0]
ml = conn.execute('SELECT COUNT(*) FROM ml_features').fetchone()[0]
print(f'musical_features: {mf}')
print(f'ml_features: {ml}')

# Sample data
print('\nSample musical_features:')
rows = conn.execute('SELECT bpm_final, key_name, energy FROM musical_features LIMIT 3').fetchall()
for r in rows:
    print(f'  BPM: {r[0]}, Key: {r[1]}, Energy: {r[2]}')

conn.close()
