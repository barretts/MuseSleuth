import type {
  OkResponse,
  PlaylistDetailResponse,
  PlaylistGenerateResponse,
  PlaylistsResponse,
  SpotifyImportCreateResponse,
  SpotifyImportPreviewResponse,
  StatusResponse,
  TrackCandidateResponse,
  TrackDetailResponse,
  TracksResponse,
} from "@/lib/types"

const API_BASE = "/api"

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  })

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const payload = await response.json()
      if (payload?.detail) detail = String(payload.detail)
    } catch {
      // ignore non-JSON error bodies
    }
    throw new Error(detail)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

function withQuery(path: string, params: URLSearchParams | Record<string, string | number | undefined>) {
  const query = params instanceof URLSearchParams
    ? new URLSearchParams(params)
    : new URLSearchParams(
        Object.entries(params).flatMap(([key, value]) =>
          value === undefined || value === "" ? [] : [[key, String(value)]],
        ),
      )
  const qs = query.toString()
  return qs ? `${path}?${qs}` : path
}

export const api = {
  getTracks(params: URLSearchParams) {
    return request<TracksResponse>(withQuery("/tracks", params))
  },

  getTrackDetail(metadataId: string) {
    return request<TrackDetailResponse>(`/tracks/${encodeURIComponent(metadataId)}`)
  },

  getStatus() {
    return request<StatusResponse>("/status")
  },

  getPlaylists(params: URLSearchParams) {
    return request<PlaylistsResponse>(withQuery("/playlists", params))
  },

  generatePlaylist(payload: Record<string, unknown>) {
    return request<PlaylistGenerateResponse>("/playlists/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    })
  },

  getPlaylistDetail(playlistId: string, sort: string) {
    return request<PlaylistDetailResponse>(
      withQuery(`/playlists/${encodeURIComponent(playlistId)}`, { sort }),
    )
  },

  deletePlaylist(playlistId: string) {
    return request<OkResponse>(`/playlists/${encodeURIComponent(playlistId)}`, {
      method: "DELETE",
    })
  },

  clonePlaylist(playlistId: string, name: string) {
    return request<PlaylistGenerateResponse>(`/playlists/${encodeURIComponent(playlistId)}/clone`, {
      method: "POST",
      body: JSON.stringify({ name }),
    })
  },

  searchTrackCandidates(playlistId: string, q: string, limit = 20) {
    return request<TrackCandidateResponse>(
      withQuery(`/playlists/${encodeURIComponent(playlistId)}/track-candidates`, { q, limit }),
    )
  },

  addTrackToPlaylist(playlistId: string, metadataId: string) {
    return request<OkResponse>(`/playlists/${encodeURIComponent(playlistId)}/tracks/add`, {
      method: "POST",
      body: JSON.stringify({ metadata_id: metadataId }),
    })
  },

  removePlaylistTrack(playlistId: string, metadataId: string) {
    return request<OkResponse>(
      `/playlists/${encodeURIComponent(playlistId)}/tracks/${encodeURIComponent(metadataId)}`,
      { method: "DELETE" },
    )
  },

  reorderPlaylistTrack(playlistId: string, metadataId: string, targetPosition: number) {
    return request<OkResponse>(
      `/playlists/${encodeURIComponent(playlistId)}/tracks/${encodeURIComponent(metadataId)}/reorder`,
      {
        method: "POST",
        body: JSON.stringify({ target_position: targetPosition }),
      },
    )
  },

  previewSpotifyImport(playlistUrl: string) {
    return request<SpotifyImportPreviewResponse>("/playlists/import-spotify/preview", {
      method: "POST",
      body: JSON.stringify({ playlist_url: playlistUrl }),
    })
  },

  createSpotifyImport(
    name: string,
    tracks: Array<{ spotify_index: number; metadata_id: string | null }>,
  ) {
    return request<SpotifyImportCreateResponse>("/playlists/import-spotify/create", {
      method: "POST",
      body: JSON.stringify({ name, tracks }),
    })
  },
}
