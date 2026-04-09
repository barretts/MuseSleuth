import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useSearchParams } from "react-router-dom"
import { Copy, Download, ListFilter, Music4, Search, Sparkles, Trash2, Upload } from "lucide-react"
import { api } from "@/lib/api"
import type { PlaylistsResponse } from "@/lib/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

const POPULARITY_OPTIONS: Array<{ value: string; label: string; min: number | null }> = [
  { value: "any", label: "Any popularity", min: null },
  { value: "niche", label: "Niche (0-999 listeners)", min: 0 },
  { value: "mainstream", label: "Mainstream (1,000-99,999 listeners)", min: 1000 },
  { value: "viral", label: "Viral (100,000+ listeners)", min: 100000 },
]

const POPULARITY_PLAYCOUNT_OPTIONS: Array<{ value: string; label: string; min: number | null }> = [
  { value: "any", label: "Any popularity", min: null },
  { value: "niche", label: "Niche (0-9,999 plays)", min: 0 },
  { value: "mainstream", label: "Mainstream (10,000-999,999 plays)", min: 10000 },
  { value: "viral", label: "Viral (1,000,000+ plays)", min: 1000000 },
]

const SEED_FACET_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "genre", label: "Genre" },
  { value: "bpm", label: "BPM" },
  { value: "energy", label: "Energy" },
  { value: "camelot", label: "Camelot Key" },
  { value: "mood", label: "Mood" },
  { value: "year", label: "Year" },
  { value: "embedding", label: "Embedding" },
  { value: "timbre", label: "Timbre" },
]

const empty: PlaylistsResponse = {
  playlists: [],
  strategies: [],
  strategies_used: [],
  genres: [],
  decades: [],
  moods: [],
  q: "",
  strategy_filter: "",
}

export function PlaylistsPage() {
  const [params, setParams] = useSearchParams()
  const seedPrefillAppliedRef = useRef(false)
  const [data, setData] = useState<PlaylistsResponse>(empty)
  const navigate = useNavigate()
  const [submitting, setSubmitting] = useState(false)
  const [busy, setBusy] = useState(false)
  const [seedSearch, setSeedSearch] = useState("")
  const [seedLoading, setSeedLoading] = useState(false)
  const [seedResults, setSeedResults] = useState<Array<{ metadata_id: string; title: string; artist: string }>>([])

  const [form, setForm] = useState({
    name: "",
    limit: 50,
    year: [1998, 2012],
    bpm: [120, 140],
    energy: [20, 90],
    danceabilityMin: 30,
    confidenceMin: 30,
    popularityTier: "any",
    popularityMetric: "listeners",
    genres: [] as string[],
    moods: [] as string[],
    moodExclude: [] as string[],
    sortBy: "popularity",
    seedId: "",
    seedIds: [] as string[],
    seedFacets: [] as string[],
    seedYearWindow: 2,
    seedYearOverride: "",
  })

  useEffect(() => {
    api.getPlaylists(params).then(setData)
  }, [params])

  useEffect(() => {
    if (seedPrefillAppliedRef.current) return

    const seedId = (params.get("seed_id") ?? "").trim()
    const seedIdsRaw = (params.get("seed_ids") ?? "").trim()
    const seedIds = seedIdsRaw
      .split(",")
      .map((value) => value.trim())
      .filter((value, index, arr) => value && arr.indexOf(value) === index)
    if (seedId && !seedIds.includes(seedId)) {
      seedIds.unshift(seedId)
    }
    if (!seedIds.length) return

    const seedTitle = (params.get("seed_title") ?? "").trim()
    const seedArtist = (params.get("seed_artist") ?? "").trim()
    const facetsRaw = (params.get("seed_facets") ?? "").trim()
    const seedYearWindowParam = Number(params.get("seed_year_window") ?? "")
    const seedYearOverrideParam = (params.get("seed_year_override") ?? "").trim()
    const allowedFacetValues = new Set(SEED_FACET_OPTIONS.map((facet) => facet.value))
    const parsedFacets = facetsRaw
      .split(",")
      .map((facet) => facet.trim().toLowerCase())
      .filter((facet) => allowedFacetValues.has(facet))
    const seedFacets = parsedFacets.length ? parsedFacets : ["genre", "bpm", "energy"]

    setForm((prev) => ({
      ...prev,
      seedId: seedIds[0] ?? "",
      seedIds,
      seedFacets,
      seedYearWindow: Number.isFinite(seedYearWindowParam) && seedYearWindowParam >= 0
        ? Math.round(seedYearWindowParam)
        : prev.seedYearWindow,
      seedYearOverride: seedYearOverrideParam || prev.seedYearOverride,
      name: prev.name || (seedTitle ? `From ${seedTitle}` : prev.name),
    }))
    setSeedResults((prev) => {
      const missingSeedIds = seedIds.filter((mid) => !prev.some((track) => track.metadata_id === mid))
      if (!missingSeedIds.length) return prev
      return [
        ...missingSeedIds.map((mid) => ({
          metadata_id: mid,
          title: mid === seedId ? (seedTitle || "Seed Track") : "Seed Track",
          artist: mid === seedId ? (seedArtist || "Unknown Artist") : "Unknown Artist",
        })),
        ...prev,
      ]
    })
    if (seedTitle || seedArtist) {
      setSeedSearch([seedTitle, seedArtist].filter(Boolean).join(" "))
    }

    seedPrefillAppliedRef.current = true
  }, [params])

  const refresh = () => api.getPlaylists(params).then(setData)

  const query = useMemo(
    () => ({
      q: params.get("q") ?? "",
      strategy_filter: params.get("strategy_filter") ?? "",
    }),
    [params],
  )

  const updateSearch = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params)
    Object.entries(patch).forEach(([key, value]) => {
      if (!value) next.delete(key)
      else next.set(key, value)
    })
    setParams(next)
  }

  const extractMetadataId = (input: string): string | null => {
    const urlMatch = input.match(/\/tracks\/([A-Z0-9]{26})(?:[?#]|$)/)
    if (urlMatch) return urlMatch[1]
    const idMatch = input.match(/^[A-Z0-9]{26}$/)
    if (idMatch) return idMatch[0]
    return null
  }

  const searchSeedTracks = async () => {
    const q = seedSearch.trim()
    if (!q) {
      setSeedResults((prev) => prev.filter((track) => form.seedIds.includes(track.metadata_id)))
      return
    }
    setSeedLoading(true)
    try {
      const directId = extractMetadataId(q)
      if (directId) {
        try {
          const detail = await api.getTrackDetail(directId)
          const track = detail.track
          const entry = {
            metadata_id: String(track.metadata_id ?? directId),
            title: String(track.title ?? "Untitled"),
            artist: String(track.artist ?? "Unknown Artist"),
          }
          setSeedResults((prev) => {
            const merged = new Map<string, { metadata_id: string; title: string; artist: string }>()
            for (const t of prev) {
              if (form.seedIds.includes(t.metadata_id)) merged.set(t.metadata_id, t)
            }
            merged.set(entry.metadata_id, entry)
            return Array.from(merged.values())
          })
          addSeedSong(directId)
          setSeedSearch("")
          return
        } catch {
          // fall through to normal search
        }
      }
      const params = new URLSearchParams()
      params.set("q", q)
      params.set("page", "1")
      const result = await api.getTracks(params)
      const nextResults = result.tracks.slice(0, 12).map((track) => ({
        metadata_id: track.metadata_id,
        title: track.title ?? "Untitled",
        artist: track.artist ?? "Unknown Artist",
      }))
      setSeedResults((prev) => {
        const merged = new Map<string, { metadata_id: string; title: string; artist: string }>()
        for (const track of prev) {
          if (form.seedIds.includes(track.metadata_id)) {
            merged.set(track.metadata_id, track)
          }
        }
        for (const track of nextResults) {
          merged.set(track.metadata_id, track)
        }
        return Array.from(merged.values())
      })
    } finally {
      setSeedLoading(false)
    }
  }

  const addSeedSong = (metadataId: string) => {
    setForm((prev) => {
      if (prev.seedIds.includes(metadataId)) return prev
      const nextSeedIds = [...prev.seedIds, metadataId]
      return {
        ...prev,
        seedIds: nextSeedIds,
        seedId: prev.seedId || metadataId,
      }
    })
  }

  const removeSeedSong = (metadataId: string) => {
    setForm((prev) => {
      const nextSeedIds = prev.seedIds.filter((item) => item !== metadataId)
      return {
        ...prev,
        seedIds: nextSeedIds,
        seedId: nextSeedIds[0] ?? "",
      }
    })
  }

  const submit = async () => {
    if (!form.name.trim()) return
    setSubmitting(true)
    try {
      const popularityOptions =
        form.popularityMetric === "play_count"
          ? POPULARITY_PLAYCOUNT_OPTIONS
          : POPULARITY_OPTIONS
      const selectedPopularity = popularityOptions.find(
        (option) => option.value === form.popularityTier,
      )
      const selectedSeedIds = form.seedIds.length
        ? form.seedIds
        : (form.seedId ? [form.seedId] : [])
      const payload = {
        // Map tier dropdown to listener_count floor used by backend SQL.
        // "Any" leaves filter unset.
        ...(selectedPopularity?.min != null
          ? { popularity_min: selectedPopularity.min }
          : {}),
        popularity_metric: form.popularityMetric,
        strategy: "custom",
        name: form.name.trim(),
        limit: form.limit,
        year_min: form.year[0],
        year_max: form.year[1],
        bpm_min: form.bpm[0],
        bpm_max: form.bpm[1],
        energy_min: form.energy[0] / 100,
        energy_max: form.energy[1] / 100,
        danceability_min: form.danceabilityMin / 100,
        confidence_min: form.confidenceMin / 100,
        genres: form.genres,
        moods: form.moods,
        mood_exclude: form.moodExclude,
        sort_by: form.sortBy,
        ...(selectedSeedIds.length
          ? {
              seed_id: selectedSeedIds[0],
              seed_ids: selectedSeedIds,
              ...(form.seedFacets.length ? { seed_facets: form.seedFacets } : {}),
              ...(form.seedFacets.includes("year") ? { seed_year_window: form.seedYearWindow } : {}),
              ...(form.seedYearOverride.trim() ? { seed_year_override: Number(form.seedYearOverride) } : {}),
            }
          : {}),
      }
      const result = await api.generatePlaylist(payload)
      navigate(`/playlists/${result.playlist_id}`)
    } finally {
      setSubmitting(false)
    }
  }

  const clonePlaylist = async (playlistId: string, name: string) => {
    setBusy(true)
    try {
      await api.clonePlaylist(playlistId, `${name} (Copy)`)
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const deletePlaylist = async (playlistId: string) => {
    if (!confirm("Delete this playlist?")) return
    setBusy(true)
    try {
      await api.deletePlaylist(playlistId)
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <Music4 className="h-5 w-5 text-primary" />
          Playlists
        </h1>
        <span className="text-sm text-muted-foreground">{data.playlists.length} playlists</span>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Search and Filter</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <div className="space-y-2">
            <Label htmlFor="playlist-search" className="inline-flex items-center gap-1.5">
              <Search className="h-3.5 w-3.5" />
              Search
            </Label>
            <Input
              id="playlist-search"
              placeholder="Playlist name..."
              value={query.q}
              onChange={(e) => updateSearch({ q: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="playlist-strategy-filter" className="inline-flex items-center gap-1.5">
              <ListFilter className="h-3.5 w-3.5" />
              Strategy
            </Label>
            <Select
              value={query.strategy_filter || "all"}
              onValueChange={(value) =>
                updateSearch({ strategy_filter: !value || value === "all" ? "" : value })
              }
            >
              <SelectTrigger id="playlist-strategy-filter">
                <SelectValue placeholder="All strategies" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All strategies</SelectItem>
                {data.strategies_used.map((strategy) => (
                  <SelectItem key={strategy} value={strategy}>
                    {strategy}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="inline-flex items-center gap-2 text-base">
            <Sparkles className="h-4 w-4 text-primary" />
            Build a Playlist
          </CardTitle>
          <Button variant="outline" size="sm" onClick={() => navigate("/playlists/import-spotify")}>
            <Upload className="h-3.5 w-3.5" />
            Import from Spotify
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div className="space-y-2 md:col-span-2">
              <Label>Playlist Name</Label>
              <Input
                value={form.name}
                onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
                placeholder="My Playlist"
              />
            </div>
            <div className="space-y-2">
              <Label>Max Tracks</Label>
              <Input
                type="number"
                min={1}
                value={form.limit}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, limit: Number(e.target.value || 50) }))
                }
              />
            </div>
          </div>

          <Tabs defaultValue={params.get("seed_id") ? "seed" : "ranges"}>
            <TabsList className="grid w-full grid-cols-5">
              <TabsTrigger value="ranges">Ranges</TabsTrigger>
              <TabsTrigger value="genres">Genres</TabsTrigger>
              <TabsTrigger value="moods">Moods</TabsTrigger>
              <TabsTrigger value="seed">From Song</TabsTrigger>
              <TabsTrigger value="advanced">Advanced</TabsTrigger>
            </TabsList>

            <TabsContent value="ranges" className="space-y-5 pt-4">
              <RangeField
                label={`Year: ${form.year[0]} - ${form.year[1]}`}
                min={1970}
                max={2030}
                value={form.year}
                onValueChange={(value) =>
                  setForm((prev) => ({
                    ...prev,
                    year: (Array.isArray(value) ? value : prev.year) as [number, number],
                  }))
                }
              />
              <RangeField
                label={`BPM: ${form.bpm[0]} - ${form.bpm[1]}`}
                min={50}
                max={220}
                value={form.bpm}
                onValueChange={(value) =>
                  setForm((prev) => ({
                    ...prev,
                    bpm: (Array.isArray(value) ? value : prev.bpm) as [number, number],
                  }))
                }
              />
              <RangeField
                label={`Energy: ${(form.energy[0] / 100).toFixed(2)} - ${(form.energy[1] / 100).toFixed(2)}`}
                min={0}
                max={100}
                value={form.energy}
                onValueChange={(value) =>
                  setForm((prev) => ({
                    ...prev,
                    energy: (Array.isArray(value) ? value : prev.energy) as [number, number],
                  }))
                }
              />
            </TabsContent>

            <TabsContent value="seed" className="space-y-4 pt-4">
              <div className="space-y-2">
                <Label htmlFor="seed-track-search">Seed Song Search</Label>
                <div className="flex gap-2">
                  <Input
                    id="seed-track-search"
                    value={seedSearch}
                    onChange={(e) => setSeedSearch(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault()
                        void searchSeedTracks()
                      }
                    }}
                    placeholder="Search by title, artist, or paste a track URL / ID"
                  />
                  <Button type="button" variant="outline" onClick={() => void searchSeedTracks()} disabled={seedLoading}>
                    {seedLoading ? "Searching..." : "Search"}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => {
                      setSeedSearch("")
                      setSeedResults((prev) => prev.filter((track) => form.seedIds.includes(track.metadata_id)))
                    }}
                    disabled={!seedSearch.trim() && !seedResults.length}
                  >
                    Clear
                  </Button>
                </div>
              </div>

              <div className="space-y-2">
                <Label>Search Results</Label>
                {seedResults.some((track) => !form.seedIds.includes(track.metadata_id)) ? (
                  <div className="max-h-56 space-y-2 overflow-auto rounded-md border p-2">
                    {seedResults
                      .filter((track) => !form.seedIds.includes(track.metadata_id))
                      .map((track) => {
                      return (
                        <div key={track.metadata_id} className="flex items-center justify-between gap-2 rounded-md border bg-muted/20 px-2 py-1.5">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">{track.title}</p>
                            <p className="truncate text-xs text-muted-foreground">{track.artist}</p>
                          </div>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={() => addSeedSong(track.metadata_id)}
                          >
                            Add
                          </Button>
                        </div>
                      )
                    })}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">No unselected results. Search by title/artist above.</p>
                )}
              </div>

              <div className="space-y-2">
                <Label>Selected Seed Songs</Label>
                {form.seedIds.length ? (
                  <div className="space-y-2 rounded-md border p-2">
                    {form.seedIds.map((mid, idx) => {
                      const track = seedResults.find((item) => item.metadata_id === mid)
                      return (
                        <div key={mid} className="flex items-center justify-between gap-2 rounded-md bg-muted/20 px-2 py-1.5">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">{track?.title ?? mid}</p>
                            <p className="truncate text-xs text-muted-foreground">
                              {track?.artist ?? "Unknown Artist"}
                              {idx === 0 ? " • primary" : ""}
                            </p>
                          </div>
                          <Button type="button" size="sm" variant="ghost" onClick={() => removeSeedSong(mid)}>
                            Remove
                          </Button>
                        </div>
                      )
                    })}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">No seed songs selected yet.</p>
                )}
              </div>

              <div className="space-y-2">
                <Label>Match Facets</Label>
                <ToggleGroup
                  multiple
                  value={form.seedFacets}
                  onValueChange={(next) => setForm((prev) => ({ ...prev, seedFacets: next }))}
                  className="flex flex-wrap justify-start gap-2"
                >
                  {SEED_FACET_OPTIONS.map((facet) => (
                    <ToggleGroupItem
                      key={facet.value}
                      value={facet.value}
                      variant="outline"
                      disabled={!form.seedIds.length}
                    >
                      {facet.label}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
                <p className="text-xs text-muted-foreground">
                  Choose one or more facets to rank tracks by similarity to the selected song.
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="seed-year-window">Year Window (+/- years)</Label>
                <Input
                  id="seed-year-window"
                  type="number"
                  min={0}
                  step={1}
                  value={form.seedYearWindow}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      seedYearWindow: Math.max(0, Number(e.target.value || 0)),
                    }))
                  }
                  disabled={!form.seedIds.length || !form.seedFacets.includes("year")}
                />
                <p className="text-xs text-muted-foreground">
                  When Year facet is enabled, only tracks within this range of the seed year are considered.
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="seed-year-override">Seed Year Override (optional)</Label>
                <Input
                  id="seed-year-override"
                  type="number"
                  min={1900}
                  max={2100}
                  step={1}
                  value={form.seedYearOverride}
                  onChange={(e) => setForm((prev) => ({ ...prev, seedYearOverride: e.target.value }))}
                  placeholder="e.g. 1999"
                  disabled={!form.seedIds.length}
                />
                <p className="text-xs text-muted-foreground">
                  Use this when the seed track metadata year is wrong (for re-releases/remasters).
                </p>
              </div>
            </TabsContent>

            <TabsContent value="genres" className="space-y-3 pt-4">
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => setForm((prev) => ({ ...prev, genres: data.genres.filter((g) => g !== "unknown") }))}>
                  Select All
                </Button>
                <Button variant="outline" size="sm" onClick={() => setForm((prev) => ({ ...prev, genres: [] }))}>
                  Clear
                </Button>
              </div>
              <ToggleGroup
                multiple
                value={form.genres}
                onValueChange={(value) => setForm((prev) => ({ ...prev, genres: value }))}
                className="flex flex-wrap justify-start gap-2"
              >
                {data.genres
                  .filter((genre) => genre !== "unknown")
                  .map((genre) => (
                    <ToggleGroupItem key={genre} value={genre} variant="outline">
                      {genre}
                    </ToggleGroupItem>
                  ))}
              </ToggleGroup>
            </TabsContent>

            <TabsContent value="moods" className="space-y-4 pt-4">
              <div className="space-y-2">
                <Label>Include Moods</Label>
                <ToggleGroup
                  multiple
                  value={form.moods}
                  onValueChange={(next) =>
                    setForm((prev) => ({
                      ...prev,
                      moods: next,
                      moodExclude: prev.moodExclude.filter((item) => !next.includes(item)),
                    }))
                  }
                  className="flex flex-wrap justify-start gap-2"
                >
                  {data.moods.map((mood) => (
                    <ToggleGroupItem
                      key={`include-${mood}`}
                      value={mood}
                      variant="outline"
                    >
                      {mood}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              </div>
              <div className="space-y-2">
                <Label>Exclude Moods</Label>
                <ToggleGroup
                  multiple
                  value={form.moodExclude}
                  onValueChange={(next) =>
                    setForm((prev) => ({
                      ...prev,
                      moodExclude: next,
                      moods: prev.moods.filter((item) => !next.includes(item)),
                    }))
                  }
                  className="flex flex-wrap justify-start gap-2"
                >
                  {data.moods.map((mood) => (
                    <ToggleGroupItem
                      key={`exclude-${mood}`}
                      value={mood}
                      variant="outline"
                    >
                      {mood}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              </div>
            </TabsContent>

            <TabsContent value="advanced" className="space-y-5 pt-4">
              <div className="space-y-2">
                <Label>Min Danceability: {(form.danceabilityMin / 100).toFixed(2)}</Label>
                <Slider
                  value={[form.danceabilityMin]}
                  min={0}
                  max={100}
                  step={1}
                  onValueChange={(value) =>
                    setForm((prev) => ({
                      ...prev,
                      danceabilityMin: Array.isArray(value) ? (value[0] ?? 0) : value,
                    }))
                  }
                />
              </div>
              <div className="space-y-2">
                <Label>Min Genre Confidence: {(form.confidenceMin / 100).toFixed(2)}</Label>
                <Slider
                  value={[form.confidenceMin]}
                  min={0}
                  max={100}
                  step={1}
                  onValueChange={(value) =>
                    setForm((prev) => ({
                      ...prev,
                      confidenceMin: Array.isArray(value) ? (value[0] ?? 0) : value,
                    }))
                  }
                />
              </div>
              <div className="space-y-2">
                <Label>Arrange By</Label>
                <Select
                  value={form.sortBy}
                  onValueChange={(value) =>
                    setForm((prev) => ({ ...prev, sortBy: value ?? prev.sortBy }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="popularity">Most Popular</SelectItem>
                    <SelectItem value="harmonic">Harmonic Mix (Camelot)</SelectItem>
                    <SelectItem value="energy_arc">Energy Arc</SelectItem>
                    <SelectItem value="bpm">BPM (Smooth)</SelectItem>
                    <SelectItem value="danceability">Danceability</SelectItem>
                    <SelectItem value="year">Chronological</SelectItem>
                    <SelectItem value="random">Shuffle</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="popularity-metric">Popularity Metric</Label>
                <Select
                  value={form.popularityMetric}
                  onValueChange={(value) =>
                    setForm((prev) => ({
                      ...prev,
                      popularityMetric: value ?? "listeners",
                      popularityTier: "any",
                    }))
                  }
                >
                  <SelectTrigger id="popularity-metric">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="listeners">Listeners (Last.fm)</SelectItem>
                    <SelectItem value="play_count">Play Count (Last.fm)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="popularity-tier">Popularity</Label>
                <Select
                  value={form.popularityTier}
                  onValueChange={(value) =>
                    setForm((prev) => ({ ...prev, popularityTier: value ?? "any" }))
                  }
                >
                  <SelectTrigger id="popularity-tier">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(form.popularityMetric === "play_count"
                      ? POPULARITY_PLAYCOUNT_OPTIONS
                      : POPULARITY_OPTIONS
                    ).map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </TabsContent>
          </Tabs>

          <div className="flex items-center justify-between border-t pt-4">
            <div className="flex items-center gap-2">
              <Badge variant="secondary">
                {form.genres.length + form.moods.length + form.moodExclude.length} tag filters active
              </Badge>
              {form.popularityTier !== "any" ? (
                <Badge variant="outline">
                  {form.popularityTier} by {form.popularityMetric === "play_count" ? "plays" : "listeners"}
                </Badge>
              ) : null}
              {form.seedIds.length && form.seedFacets.length ? (
                <Badge variant="outline">Seed match: {form.seedIds.length} song(s), {form.seedFacets.length} facet(s)</Badge>
              ) : null}
            </div>
            <Button onClick={submit} disabled={submitting || !form.name.trim()}>
              <Sparkles className="h-3.5 w-3.5" />
              {submitting ? "Generating..." : "Generate Playlist"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Strategy</TableHead>
                <TableHead className="text-right">Tracks</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="w-48">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.playlists.length ? (
                data.playlists.map((playlist) => (
                  <TableRow key={playlist.playlist_id} className="odd:bg-muted/20 even:bg-background hover:bg-muted/40">
                    <TableCell>
                      <Link to={`/playlists/${playlist.playlist_id}`} className="font-medium hover:underline">
                        {playlist.name}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{playlist.strategy}</Badge>
                    </TableCell>
                    <TableCell className="text-right">{playlist.track_count}</TableCell>
                    <TableCell>{playlist.created_at}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <a
                          href={`/api/playlists/${playlist.playlist_id}/export`}
                          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                        >
                          <Download className="h-3 w-3" />
                          M3U8
                        </a>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => clonePlaylist(playlist.playlist_id, playlist.name)}
                          disabled={busy}
                        >
                          <Copy className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => deletePlaylist(playlist.playlist_id)}
                          disabled={busy}
                        >
                          <Trash2 className="h-3.5 w-3.5 text-[var(--color-highlight)]" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell colSpan={5}>No playlists found.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

function RangeField({
  label,
  min,
  max,
  value,
  onValueChange,
}: {
  label: string
  min: number
  max: number
  value: number[]
  onValueChange: (value: number | readonly number[]) => void
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <Slider min={min} max={max} step={1} value={value} onValueChange={onValueChange} />
    </div>
  )
}
