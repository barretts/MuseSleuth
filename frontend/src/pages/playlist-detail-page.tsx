import { useEffect, useRef, useState } from "react"
import { Link, useParams, useSearchParams } from "react-router-dom"
import { ArrowLeft, Copy, Download, GripVertical, PlayCircle, Plus, Search, Sparkles, Trash2 } from "lucide-react"
import { api } from "@/lib/api"
import type { PlaylistDetailResponse, TrackCandidate } from "@/lib/types"
import { CamelotBadge, EnergyBadge, GenreBadge } from "@/components/badges"
import { useAudioPlayer } from "@/components/audio-player"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Toggle } from "@/components/ui/toggle"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

export function PlaylistDetailPage() {
  const { playlistId = "" } = useParams()
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState<PlaylistDetailResponse | null>(null)
  const [trackQuery, setTrackQuery] = useState("")
  const [candidates, setCandidates] = useState<TrackCandidate[]>([])
  const [busy, setBusy] = useState(false)
  const [draggingTrackId, setDraggingTrackId] = useState<string | null>(null)
  const [dragOverTrackId, setDragOverTrackId] = useState<string | null>(null)
  const [autoplayChain, setAutoplayChain] = useState(true)
  const [scrollWidth, setScrollWidth] = useState(0)
  const [clientWidth, setClientWidth] = useState(0)
  const [showStickyScrollbar, setShowStickyScrollbar] = useState(false)
  const sort = params.get("sort") ?? "position"
  const { current, play } = useAudioPlayer()
  const tableScrollRef = useRef<HTMLDivElement | null>(null)
  const stickyScrollRef = useRef<HTMLDivElement | null>(null)

  const loadDetail = () => {
    api.getPlaylistDetail(playlistId, sort).then(setData)
  }

  useEffect(() => {
    loadDetail()
  }, [playlistId, sort])

  useEffect(() => {
    if (!trackQuery.trim()) {
      setCandidates([])
      return
    }
    api.searchTrackCandidates(playlistId, trackQuery).then((res) => setCandidates(res.tracks))
  }, [playlistId, trackQuery])

  useEffect(() => {
    const source = tableScrollRef.current
    if (!source) return

    const recompute = () => {
      const width = source.scrollWidth
      const client = source.clientWidth
      setScrollWidth(width)
      setClientWidth(client)
      setShowStickyScrollbar(width > client + 1)
    }

    const onSourceScroll = () => {
      const sticky = stickyScrollRef.current
      if (!sticky) return
      if (sticky.scrollLeft !== source.scrollLeft) {
        sticky.scrollLeft = source.scrollLeft
      }
    }

    source.addEventListener("scroll", onSourceScroll, { passive: true })
    const resizeObserver = new ResizeObserver(recompute)
    resizeObserver.observe(source)
    recompute()

    return () => {
      source.removeEventListener("scroll", onSourceScroll)
      resizeObserver.disconnect()
    }
  }, [data?.tracks.length, sort])

  if (!data) return <p>Loading...</p>

  const deletePlaylist = async () => {
    if (!confirm("Delete this playlist? This cannot be undone.")) return
    await api.deletePlaylist(playlistId)
    window.location.assign("/playlists")
  }

  const clonePlaylist = async () => {
    const name = prompt("Name for cloned playlist", `${data.playlist.name} (Copy)`)
    if (!name) return
    const result = await api.clonePlaylist(playlistId, name)
    window.location.assign(`/playlists/${result.playlist_id}`)
  }

  const reorderTrack = async (metadataId: string, targetPosition: number) => {
    setBusy(true)
    try {
      await api.reorderPlaylistTrack(playlistId, metadataId, targetPosition)
      loadDetail()
    } finally {
      setBusy(false)
    }
  }

  const removeTrack = async (metadataId: string) => {
    setBusy(true)
    try {
      await api.removePlaylistTrack(playlistId, metadataId)
      loadDetail()
      if (trackQuery.trim()) {
        const res = await api.searchTrackCandidates(playlistId, trackQuery)
        setCandidates(res.tracks)
      }
    } finally {
      setBusy(false)
    }
  }

  const addTrack = async (metadataId: string) => {
    setBusy(true)
    try {
      await api.addTrackToPlaylist(playlistId, metadataId)
      loadDetail()
      const res = await api.searchTrackCandidates(playlistId, trackQuery)
      setCandidates(res.tracks)
    } finally {
      setBusy(false)
    }
  }

  const playFromPlaylistIndex = (index: number) => {
    const track = data.tracks[index]
    if (!track) return
    const label = `${track.artist ?? "-"} - ${track.title ?? "-"}`
    if (!autoplayChain) {
      play(track.metadata_id, label)
      return
    }
    play(track.metadata_id, label, () => {
      if (index + 1 < data.tracks.length) playFromPlaylistIndex(index + 1)
    })
  }

  return (
    <div className="flex flex-col gap-4">
      <Link to="/playlists" className="text-sm text-muted-foreground hover:text-foreground">
        <span className="inline-flex items-center gap-1.5">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to playlists
        </span>
      </Link>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle className="text-2xl">{data.playlist.name}</CardTitle>
            <p className="text-sm text-muted-foreground">{data.playlist.track_count} tracks</p>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="secondary">{data.playlist.strategy}</Badge>
            <Button variant="outline" size="sm" onClick={clonePlaylist}>
              <Copy className="h-3.5 w-3.5" />
              Clone
            </Button>
            <Button variant="destructive" size="sm" onClick={deletePlaylist}>
              <Trash2 className="h-3.5 w-3.5" />
              Delete
            </Button>
            <a
              href={`/api/playlists/${playlistId}/export`}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              <Download className="h-3.5 w-3.5" />
              Export M3U8
            </a>
          </div>
        </CardHeader>
      </Card>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="Total Duration" value={data.stats.total_duration} />
        <StatCard
          label="BPM Range"
          value={
            data.stats.bpm_min != null && data.stats.bpm_max != null
              ? `${data.stats.bpm_min} - ${data.stats.bpm_max}`
              : "-"
          }
        />
        <StatCard
          label="Dominant Keys"
          value={data.stats.dominant_keys.map(([key]) => key).join(", ") || "-"}
        />
        <StatCard label="Tracks" value={String(data.tracks.length)} />
      </div>

      <div className="flex items-center justify-between rounded-md border bg-card/60 px-3 py-2">
        <p className="text-sm text-muted-foreground">
          Playback mode:{" "}
          <span className="font-medium text-foreground">
            {autoplayChain ? "Autoplay next track" : "Single track only"}
          </span>
        </p>
        <Toggle
          pressed={autoplayChain}
          onPressedChange={setAutoplayChain}
          aria-label="Toggle playlist autoplay chain"
          className="gap-1.5"
        >
          <Sparkles className="h-3.5 w-3.5" />
          Chain
        </Toggle>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Add track to playlist</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="track-search-candidates" className="inline-flex items-center gap-1.5">
              <Search className="h-3.5 w-3.5" />
              Search tracks by title, artist, album, or id
            </Label>
            <Input
              id="track-search-candidates"
              value={trackQuery}
              onChange={(e) => setTrackQuery(e.target.value)}
              placeholder="Type to search..."
            />
          </div>
          {trackQuery.trim() ? (
            <div className="space-y-2">
              {candidates.slice(0, 10).map((track) => (
                <div
                  key={track.metadata_id}
                  className="flex items-center justify-between rounded-md border bg-muted/20 px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{track.title ?? "Untitled"}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {track.artist ?? "-"} · {track.album ?? "-"}
                    </p>
                  </div>
                  <Button size="sm" onClick={() => addTrack(track.metadata_id)} disabled={busy}>
                    <Plus className="h-3.5 w-3.5" />
                    Add
                  </Button>
                </div>
              ))}
              {candidates.length === 0 ? (
                <p className="text-sm text-muted-foreground">No matching tracks found.</p>
              ) : null}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <div className="flex items-center gap-2">
        <div className="space-y-2">
          <label htmlFor="playlist-sort" className="text-sm text-muted-foreground">
            Sort by
          </label>
          <Select
            value={sort}
            onValueChange={(value) => {
              if (!value) return
              const next = new URLSearchParams(params)
              next.set("sort", value)
              setParams(next)
            }}
          >
            <SelectTrigger id="playlist-sort" className="w-64">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {data.sort_options.map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {sort !== "position" ? (
          <p className="text-xs text-muted-foreground">
            Reordering is enabled only in Original Order view.
          </p>
        ) : null}
      </div>

      <Card>
        <CardContent className="p-0">
          <div ref={tableScrollRef} className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10" />
                  <TableHead>#</TableHead>
                  <TableHead />
                  <TableHead>Title</TableHead>
                  <TableHead>Artist</TableHead>
                  <TableHead>Album</TableHead>
                  <TableHead>BPM</TableHead>
                  <TableHead>Key</TableHead>
                  <TableHead>Genre</TableHead>
                  <TableHead>Energy</TableHead>
                  <TableHead className="w-16">Manage</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.tracks.map((track, idx) => (
                  <TableRow
                    key={`${track.metadata_id}-${idx}`}
                    className={`odd:bg-muted/20 even:bg-background hover:bg-muted/40 ${
                      dragOverTrackId === track.metadata_id ? "bg-accent/50" : ""
                    }`}
                    draggable={sort === "position"}
                    onDragStart={(e) => {
                      if (sort !== "position") return
                      e.dataTransfer.effectAllowed = "move"
                      e.dataTransfer.setData("text/plain", track.metadata_id)
                      setDraggingTrackId(track.metadata_id)
                    }}
                    onDragOver={(e) => {
                      if (sort !== "position") return
                      e.preventDefault()
                      setDragOverTrackId(track.metadata_id)
                    }}
                    onDragLeave={() => {
                      if (sort !== "position") return
                      setDragOverTrackId((prev) => (prev === track.metadata_id ? null : prev))
                    }}
                    onDrop={async (e) => {
                      if (sort !== "position") return
                      e.preventDefault()
                      const draggedId = e.dataTransfer.getData("text/plain")
                      setDragOverTrackId(null)
                      setDraggingTrackId(null)
                      if (!draggedId || draggedId === track.metadata_id) return
                      await reorderTrack(draggedId, idx + 1)
                    }}
                    onDragEnd={() => {
                      setDragOverTrackId(null)
                      setDraggingTrackId(null)
                    }}
                  >
                    <TableCell>
                      <span
                        className={`inline-flex items-center text-muted-foreground ${
                          sort === "position" ? "cursor-grab" : "cursor-not-allowed opacity-50"
                        }`}
                        title={
                          sort === "position"
                            ? "Drag to reorder"
                            : "Switch to Original Order to reorder"
                        }
                      >
                        <GripVertical className="h-4 w-4" />
                      </span>
                    </TableCell>
                    <TableCell>{idx + 1}</TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                      onClick={() => playFromPlaylistIndex(idx)}
                      >
                        <PlayCircle className="h-3.5 w-3.5" />
                        {current?.metadataId === track.metadata_id ? "Stop" : "Play"}
                      </Button>
                    </TableCell>
                    <TableCell>
                      <Link to={`/tracks/${track.metadata_id}`} className="font-medium hover:underline">
                        {track.title ?? "Untitled"}
                      </Link>
                    </TableCell>
                    <TableCell>{track.artist ?? "-"}</TableCell>
                    <TableCell>{track.album ?? "-"}</TableCell>
                    <TableCell>{track.bpm_final ? Math.round(track.bpm_final) : "-"}</TableCell>
                    <TableCell>
                      <CamelotBadge value={track.camelot_key} />
                    </TableCell>
                    <TableCell>
                      <GenreBadge value={track.genre_primary} />
                    </TableCell>
                    <TableCell>
                      <EnergyBadge value={track.energy_tier} />
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => removeTrack(track.metadata_id)}
                        disabled={busy || draggingTrackId === track.metadata_id}
                      >
                        <Trash2 className="h-3.5 w-3.5 text-[var(--color-highlight)]" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {showStickyScrollbar ? (
        <div
          ref={stickyScrollRef}
          className={`fixed inset-x-4 z-40 overflow-x-auto rounded-md border bg-card/95 p-1 backdrop-blur ${
            current ? "bottom-14" : "bottom-2"
          }`}
          onScroll={(e) => {
            const source = tableScrollRef.current
            if (!source) return
            if (source.scrollLeft !== e.currentTarget.scrollLeft) {
              source.scrollLeft = e.currentTarget.scrollLeft
            }
          }}
        >
          <div
            style={{ width: scrollWidth, minWidth: scrollWidth, height: 1 }}
            className={clientWidth < scrollWidth ? "bg-transparent" : "hidden"}
          />
        </div>
      ) : null}
    </div>
  )
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="p-4 text-center">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-lg font-semibold">{value}</p>
      </CardContent>
    </Card>
  )
}
