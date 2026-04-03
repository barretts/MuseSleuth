import { type ReactNode, useEffect, useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { Disc3, Filter, PlayCircle, Search, SlidersHorizontal } from "lucide-react"
import { api } from "@/lib/api"
import type { TracksResponse } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { CamelotBadge, EnergyBadge, GenreBadge } from "@/components/badges"
import { useAudioPlayer } from "@/components/audio-player"

const empty: TracksResponse = {
  tracks: [],
  q: "",
  decade: "",
  bpm_bucket: "",
  energy_tier: "",
  camelot_key: "",
  genre: "",
  vocal_type: "",
  sort: "title",
  order: "asc",
  page: 1,
  total_pages: 1,
  total: 0,
  decades: [],
  bpm_buckets: [],
  energy_tiers: [],
  camelot_keys: [],
  genres: [],
  vocal_types: [],
}

export function TracksPage() {
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState<TracksResponse>(empty)
  const [loading, setLoading] = useState(true)
  const { current, play } = useAudioPlayer()

  useEffect(() => {
    let active = true
    setLoading(true)
    api
      .getTracks(params)
      .then((res) => {
        if (active) setData(res)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [params])

  const query = useMemo(
    () => ({
      q: params.get("q") ?? "",
      decade: params.get("decade") ?? "",
      bpm_bucket: params.get("bpm_bucket") ?? "",
      energy_tier: params.get("energy_tier") ?? "",
      camelot_key: params.get("camelot_key") ?? "",
      genre: params.get("genre") ?? "",
      vocal_type: params.get("vocal_type") ?? "",
      sort: params.get("sort") ?? "title",
      order: params.get("order") ?? "asc",
      page: Number(params.get("page") ?? "1"),
    }),
    [params],
  )

  const update = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params)
    Object.entries(patch).forEach(([key, value]) => {
      if (!value) next.delete(key)
      else next.set(key, value)
    })
    if (!patch.page) next.set("page", "1")
    setParams(next)
  }

  const sortBy = (col: string) => {
    const nextOrder = query.sort === col && query.order === "asc" ? "desc" : "asc"
    update({ sort: col, order: nextOrder, page: "1" })
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <Disc3 className="h-5 w-5 text-primary" />
          Tracks
        </h1>
        <span className="text-sm text-muted-foreground">{data.total} tracks</span>
      </div>

      <Card>
        <CardContent className="grid grid-cols-1 gap-3 p-4 md:grid-cols-4 lg:grid-cols-8">
          <div className="space-y-2 lg:col-span-2">
            <Label htmlFor="track-search" className="inline-flex items-center gap-1.5">
              <Search className="h-3.5 w-3.5" />
              Search
            </Label>
            <Input
              id="track-search"
              placeholder="Title, artist, album..."
              value={query.q}
              onChange={(e) => update({ q: e.target.value })}
            />
          </div>
          <FilterSelect
            id="track-genre"
            label="Genre"
            icon={<Filter className="h-3.5 w-3.5" />}
            value={query.genre}
            values={data.genres}
            placeholder="Genre"
            onValueChange={(value) => update({ genre: value })}
          />
          <FilterSelect
            id="track-vocal-type"
            label="Vocal Type"
            icon={<SlidersHorizontal className="h-3.5 w-3.5" />}
            value={query.vocal_type}
            values={data.vocal_types}
            placeholder="Vocal Type"
            onValueChange={(value) => update({ vocal_type: value })}
          />
          <FilterSelect
            id="track-decade"
            label="Decade"
            icon={<Filter className="h-3.5 w-3.5" />}
            value={query.decade}
            values={data.decades}
            placeholder="Decade"
            onValueChange={(value) => update({ decade: value })}
          />
          <FilterSelect
            id="track-bpm"
            label="BPM"
            icon={<Filter className="h-3.5 w-3.5" />}
            value={query.bpm_bucket}
            values={data.bpm_buckets}
            placeholder="BPM"
            onValueChange={(value) => update({ bpm_bucket: value })}
          />
          <FilterSelect
            id="track-energy"
            label="Energy"
            icon={<Filter className="h-3.5 w-3.5" />}
            value={query.energy_tier}
            values={data.energy_tiers}
            placeholder="Energy"
            onValueChange={(value) => update({ energy_tier: value })}
          />
          <FilterSelect
            id="track-camelot"
            label="Camelot Key"
            icon={<Filter className="h-3.5 w-3.5" />}
            value={query.camelot_key}
            values={data.camelot_keys}
            placeholder="Camelot Key"
            onValueChange={(value) => update({ camelot_key: value })}
          />
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead />
                {[
                  ["Title", "title"],
                  ["Artist", "artist"],
                  ["Album", "album"],
                  ["Year", "year"],
                  ["BPM", "bpm_final"],
                  ["Key", "camelot_key"],
                  ["Genre", "genre"],
                  ["Energy", "energy_tier"],
                ].map(([label, key]) => (
                  <TableHead key={key}>
                    <Button variant="ghost" size="sm" onClick={() => sortBy(key)}>
                      {label}
                    </Button>
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={9}>Loading...</TableCell>
                </TableRow>
              ) : data.tracks.length ? (
                data.tracks.map((track) => (
                  <TableRow key={track.metadata_id} className="odd:bg-muted/20 even:bg-background hover:bg-muted/40">
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => play(track.metadata_id, `${track.artist ?? "-"} - ${track.title ?? "-"}`)}
                      >
                        <PlayCircle className="h-3.5 w-3.5" />
                        {current?.metadataId === track.metadata_id ? "Stop" : "Play"}
                      </Button>
                    </TableCell>
                    <TableCell>
                      <Link to={`/tracks/${track.metadata_id}`} className="font-medium hover:underline">
                        {track.title ?? track.filename ?? "-"}
                      </Link>
                    </TableCell>
                    <TableCell>{track.artist ?? "-"}</TableCell>
                    <TableCell>{track.album ?? "-"}</TableCell>
                    <TableCell>{track.year ?? "-"}</TableCell>
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
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell colSpan={9}>No tracks found.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <div className="flex items-center justify-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={query.page <= 1}
          onClick={() => update({ page: String(Math.max(query.page - 1, 1)) })}
        >
          Prev
        </Button>
        <span className="text-sm text-muted-foreground">
          Page {query.page} of {data.total_pages}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={query.page >= data.total_pages}
          onClick={() => update({ page: String(Math.min(query.page + 1, data.total_pages)) })}
        >
          Next
        </Button>
      </div>
    </div>
  )
}

function FilterSelect({
  id,
  label,
  icon,
  value,
  values,
  placeholder,
  onValueChange,
}: {
  id: string
  label: string
  icon?: ReactNode
  value: string
  values: string[]
  placeholder: string
  onValueChange: (value: string) => void
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id} className="inline-flex items-center gap-1.5">
        {icon}
        {label}
      </Label>
      <Select
        value={value || "all"}
        onValueChange={(v) => onValueChange(!v || v === "all" ? "" : v)}
      >
        <SelectTrigger id={id}>
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All</SelectItem>
          {values.map((item) => (
            <SelectItem key={item} value={item}>
              {item}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
