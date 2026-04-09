import { useEffect, useRef, useState } from "react"
import { useDebounce } from "@/hooks/use-debounce"
import { Link, useNavigate } from "react-router-dom"
import { ArrowLeft, CheckCircle2, CircleAlert, CircleDashed, Music4, Search, X } from "lucide-react"
import { api } from "@/lib/api"
import type { SpotifyImportTrack, TrackCandidate } from "@/lib/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

type Phase = "input" | "review" | "done"

interface TrackOverride {
  [spotifyIndex: number]: TrackCandidate | null
}

function ConfidenceBadge({ confidence }: { confidence: SpotifyImportTrack["match_confidence"] }) {
  if (confidence === "exact")
    return (
      <Badge variant="secondary" className="gap-1 bg-green-500/15 text-green-700 dark:text-green-400">
        <CheckCircle2 className="h-3 w-3" />
        Exact
      </Badge>
    )
  if (confidence === "fuzzy")
    return (
      <Badge variant="secondary" className="gap-1 bg-yellow-500/15 text-yellow-700 dark:text-yellow-400">
        <CircleAlert className="h-3 w-3" />
        Fuzzy
      </Badge>
    )
  return (
    <Badge variant="secondary" className="gap-1 bg-muted text-muted-foreground">
      <CircleDashed className="h-3 w-3" />
      No match
    </Badge>
  )
}

function TrackSearchPanel({
  onSelect,
  onClose,
}: {
  onSelect: (track: TrackCandidate) => void
  onClose: () => void
}) {
  const [query, setQuery] = useState("")
  const debouncedQuery = useDebounce(query, 300)
  const [candidates, setCandidates] = useState<TrackCandidate[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!debouncedQuery.trim()) {
      setCandidates([])
      return
    }
    const qs = new URLSearchParams({ q: debouncedQuery })
    api.getTracks(qs).then((res) =>
      setCandidates(
        res.tracks.slice(0, 10).map((t) => ({
          metadata_id: t.metadata_id,
          title: t.title,
          artist: t.artist,
          album: t.album ?? null,
          bpm_final: t.bpm_final ?? null,
          camelot_key: t.camelot_key ?? null,
        })),
      ),
    )
  }, [debouncedQuery])

  return (
    <div className="mt-2 space-y-2 rounded-md border bg-muted/30 p-3">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            ref={inputRef}
            className="pl-8 text-sm"
            placeholder="Search by title, artist, album..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Button variant="ghost" size="sm" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      {candidates.length > 0 ? (
        <div className="space-y-1">
          {candidates.map((t) => (
            <div
              key={t.metadata_id}
              className="flex items-center justify-between rounded-md border bg-background px-3 py-2"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{t.title ?? "Untitled"}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {t.artist ?? "-"} · {t.album ?? "-"}
                </p>
              </div>
              <Button size="sm" variant="outline" onClick={() => onSelect(t)}>
                Use this
              </Button>
            </div>
          ))}
        </div>
      ) : query.trim() ? (
        <p className="text-sm text-muted-foreground">No local tracks found.</p>
      ) : null}
    </div>
  )
}

export function SpotifyImportPage() {
  const navigate = useNavigate()
  const [phase, setPhase] = useState<Phase>("input")
  const [playlistUrl, setPlaylistUrl] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [spotifyName, setSpotifyName] = useState("")
  const [tracks, setTracks] = useState<SpotifyImportTrack[]>([])
  const [overrides, setOverrides] = useState<TrackOverride>({})
  const [openSearchIndex, setOpenSearchIndex] = useState<number | null>(null)
  const [playlistName, setPlaylistName] = useState("")
  const [submitting, setSubmitting] = useState(false)

  const effectiveMatch = (track: SpotifyImportTrack): TrackCandidate | null => {
    if (track.spotify_index in overrides) return overrides[track.spotify_index]
    return track.match
  }

  const effectiveConfidence = (track: SpotifyImportTrack): SpotifyImportTrack["match_confidence"] => {
    if (track.spotify_index in overrides) {
      return overrides[track.spotify_index] ? "exact" : "none"
    }
    return track.match_confidence
  }

  const matchedCount = tracks.filter((t) => effectiveMatch(t) !== null).length
  const skippedCount = tracks.length - matchedCount

  const handlePreview = async () => {
    if (!playlistUrl.trim()) return
    setLoading(true)
    setError(null)
    try {
      const result = await api.previewSpotifyImport(playlistUrl.trim())
      setSpotifyName(result.spotify_playlist_name)
      setTracks(result.tracks)
      setOverrides({})
      setPlaylistName(result.spotify_playlist_name)
      setPhase("review")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch Spotify playlist")
    } finally {
      setLoading(false)
    }
  }

  const handleCreate = async () => {
    if (!playlistName.trim() || matchedCount === 0) return
    setSubmitting(true)
    setError(null)
    try {
      const tracksPayload = tracks.map((t) => ({
        spotify_index: t.spotify_index,
        metadata_id: effectiveMatch(t)?.metadata_id ?? null,
      }))
      const result = await api.createSpotifyImport(playlistName.trim(), tracksPayload)
      navigate(`/playlists/${result.playlist_id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create playlist")
    } finally {
      setSubmitting(false)
    }
  }

  const toggleSearch = (index: number) => {
    setOpenSearchIndex((prev) => (prev === index ? null : index))
  }

  const applyOverride = (index: number, track: TrackCandidate | null) => {
    setOverrides((prev) => ({ ...prev, [index]: track }))
    setOpenSearchIndex(null)
  }

  return (
    <div className="flex flex-col gap-4">
      <Link to="/playlists" className="text-sm text-muted-foreground hover:text-foreground">
        <span className="inline-flex items-center gap-1.5">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to playlists
        </span>
      </Link>

      <div className="flex items-center gap-2">
        <Music4 className="h-5 w-5 text-primary" />
        <h1 className="text-2xl font-bold">Import from Spotify</h1>
      </div>

      {phase === "input" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Paste a Spotify playlist URL</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="spotify-url">Spotify Playlist URL or ID</Label>
              <Input
                id="spotify-url"
                placeholder="https://open.spotify.com/playlist/..."
                value={playlistUrl}
                onChange={(e) => setPlaylistUrl(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handlePreview()
                }}
              />
              <p className="text-xs text-muted-foreground">
                Public playlists only. Requires{" "}
                <code className="rounded bg-muted px-1 py-0.5 text-xs">MUSESLEUTH_SPOTIFY_CLIENT_ID</code> and{" "}
                <code className="rounded bg-muted px-1 py-0.5 text-xs">MUSESLEUTH_SPOTIFY_CLIENT_SECRET</code> to be
                set.
              </p>
            </div>
            {error ? <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p> : null}
            <Button onClick={handlePreview} disabled={loading || !playlistUrl.trim()}>
              {loading ? "Fetching playlist..." : "Preview Playlist"}
            </Button>
          </CardContent>
        </Card>
      )}

      {phase === "review" && (
        <>
          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-4">
              <div>
                <p className="text-lg font-semibold">{spotifyName}</p>
                <p className="text-sm text-muted-foreground">
                  {tracks.length} Spotify tracks &mdash;{" "}
                  <span className="text-foreground">{matchedCount} matched</span>
                  {skippedCount > 0 ? (
                    <span className="text-yellow-600 dark:text-yellow-400">, {skippedCount} will be skipped</span>
                  ) : null}
                </p>
              </div>
              <Button variant="outline" size="sm" onClick={() => { setPhase("input"); setError(null) }}>
                Change playlist
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-10">#</TableHead>
                      <TableHead>Spotify Track</TableHead>
                      <TableHead>Matched Local Track</TableHead>
                      <TableHead className="w-28">Confidence</TableHead>
                      <TableHead className="w-32">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {tracks.map((track) => {
                      const match = effectiveMatch(track)
                      const conf = effectiveConfidence(track)
                      const isOpen = openSearchIndex === track.spotify_index
                      return (
                        <TableRow
                          key={track.spotify_index}
                          className="odd:bg-muted/20 even:bg-background hover:bg-muted/40 align-top"
                        >
                          <TableCell className="pt-3 text-muted-foreground">{track.spotify_index + 1}</TableCell>
                          <TableCell className="pt-3">
                            <p className="text-sm font-medium">{track.spotify_title ?? "Unknown"}</p>
                            <p className="text-xs text-muted-foreground">
                              {track.spotify_artist ?? "-"}
                              {track.spotify_album ? ` · ${track.spotify_album}` : ""}
                            </p>
                          </TableCell>
                          <TableCell className="pt-3">
                            {match ? (
                              <div>
                                <p className="text-sm font-medium">{match.title ?? "Untitled"}</p>
                                <p className="text-xs text-muted-foreground">
                                  {match.artist ?? "-"}
                                  {match.album ? ` · ${match.album}` : ""}
                                </p>
                              </div>
                            ) : (
                              <span className="text-sm text-muted-foreground">No match</span>
                            )}
                            {isOpen ? (
                              <TrackSearchPanel
                                onSelect={(t) => applyOverride(track.spotify_index, t)}
                                onClose={() => setOpenSearchIndex(null)}
                              />
                            ) : null}
                          </TableCell>
                          <TableCell className="pt-3">
                            <ConfidenceBadge confidence={conf} />
                          </TableCell>
                          <TableCell className="pt-2">
                            <div className="flex flex-col gap-1">
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => toggleSearch(track.spotify_index)}
                              >
                                <Search className="h-3 w-3" />
                                {match ? "Replace" : "Search"}
                              </Button>
                              {match ? (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="text-xs text-muted-foreground"
                                  onClick={() => applyOverride(track.spotify_index, null)}
                                >
                                  Clear
                                </Button>
                              ) : null}
                            </div>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Create Playlist</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="import-playlist-name">Playlist Name</Label>
                <Input
                  id="import-playlist-name"
                  value={playlistName}
                  onChange={(e) => setPlaylistName(e.target.value)}
                  placeholder="Playlist name..."
                />
              </div>
              {skippedCount > 0 ? (
                <p className="text-sm text-yellow-600 dark:text-yellow-400">
                  {skippedCount} unmatched track{skippedCount !== 1 ? "s" : ""} will be skipped.
                </p>
              ) : null}
              {error ? <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p> : null}
              <Button
                onClick={handleCreate}
                disabled={submitting || matchedCount === 0 || !playlistName.trim()}
              >
                {submitting ? "Creating..." : `Create Playlist (${matchedCount} tracks)`}
              </Button>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
