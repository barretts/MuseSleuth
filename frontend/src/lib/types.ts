export type Nullable<T> = T | null

export interface TrackListItem {
  metadata_id: string
  title: string | null
  artist: string | null
  album: string | null
  year: string | null
  filename?: string | null
  bpm_final?: number | null
  key_name?: string | null
  key_mode?: string | null
  energy?: number | null
  camelot_key?: string | null
  energy_tier?: string | null
  decade_bucket?: string | null
  bpm_bucket?: string | null
  genre_primary?: string | null
  vocal_type?: string | null
  mood_tags?: string | null
}

export interface TracksResponse {
  tracks: TrackListItem[]
  q: string
  decade: string
  bpm_bucket: string
  energy_tier: string
  camelot_key: string
  genre: string
  vocal_type: string
  sort: string
  order: string
  page: number
  total_pages: number
  total: number
  decades: string[]
  bpm_buckets: string[]
  energy_tiers: string[]
  camelot_keys: string[]
  genres: string[]
  vocal_types: string[]
}

export interface GenreTag {
  tag_type: string
  tag_value: string
  source: string
  confidence: number | null
}

export interface ExternalId {
  source: string
  external_id: string
  confidence: number | null
}

export interface TrackDetailResponse {
  track: Record<string, unknown>
  tech: Record<string, unknown> | null
  musical: Record<string, unknown> | null
  signals: Record<string, unknown> | null
  ml: Record<string, unknown> | null
  genres: GenreTag[]
  genres_by_source: Record<string, GenreTag[]>
  ext_ids: ExternalId[]
  artist: Record<string, unknown>
  track_ext: Record<string, unknown>
  artist_stats_rows: Record<string, unknown>[]
  track_stats_rows: Record<string, unknown>[]
}

export interface StatusStats {
  total_tracks: number
  pending_jobs: number
  running_jobs: number
  done_jobs: number
  failed_jobs: number
  completion_pct: number
}

export interface StatusStage {
  name: string
  pending: number
  running: number
  done: number
  failed: number
}

export interface StatusResponse {
  stats: StatusStats
  stages: StatusStage[]
}

export interface PlaylistSummary {
  playlist_id: string
  name: string
  strategy: string
  track_count: number
  created_at: string
}

export interface PlaylistsResponse {
  playlists: PlaylistSummary[]
  strategies: string[]
  strategies_used: string[]
  genres: string[]
  decades: string[]
  moods: string[]
  q: string
  strategy_filter: string
}

export interface PlaylistGenerateResponse {
  playlist_id: string
}

export interface PlaylistTrack {
  position?: number
  metadata_id: string
  title: string | null
  artist: string | null
  album: string | null
  year?: string | null
  bpm_final?: number | null
  energy?: number | null
  camelot_key?: string | null
  energy_tier?: string | null
  genre_primary?: string | null
  duration_ms?: number | null
}

export interface PlaylistStats {
  total_duration: string
  bpm_min: number | null
  bpm_max: number | null
  dominant_keys: Array<[string, number]>
}

export interface PlaylistDetailResponse {
  playlist: Record<string, unknown> & {
    playlist_id: string
    name: string
    strategy: string
    track_count: number
  }
  tracks: PlaylistTrack[]
  stats: PlaylistStats
  sort: string
  sort_options: Array<[string, string]>
}

export interface TrackCandidate {
  metadata_id: string
  title: string | null
  artist: string | null
  album: string | null
  bpm_final?: number | null
  camelot_key?: string | null
}

export interface TrackCandidateResponse {
  tracks: TrackCandidate[]
}

export interface OkResponse {
  ok: boolean
}

export interface SpotifyImportTrack {
  spotify_index: number
  spotify_title: string | null
  spotify_artist: string | null
  spotify_album: string | null
  spotify_track_id: string | null
  duration_ms: number | null
  match: TrackCandidate | null
  match_confidence: "exact" | "fuzzy" | "none"
}

export interface SpotifyImportPreviewResponse {
  spotify_playlist_name: string
  spotify_playlist_id: string
  tracks: SpotifyImportTrack[]
}

export interface SpotifyImportCreateResponse {
  playlist_id: string
}
