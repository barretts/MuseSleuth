import { useEffect, useMemo, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { ArrowLeft, PlayCircle, Sparkles } from "lucide-react"
import { api } from "@/lib/api"
import type { TrackDetailResponse } from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useAudioPlayer } from "@/components/audio-player"

export function TrackDetailPage() {
  const { metadataId = "" } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState<TrackDetailResponse | null>(null)
  const { current, play } = useAudioPlayer()

  useEffect(() => {
    api.getTrackDetail(metadataId).then(setData)
  }, [metadataId])

  const title = String(data?.track?.title ?? "Untitled")
  const artist = String(data?.track?.artist ?? "Unknown artist")

  const genreSources = useMemo(
    () => Object.entries(data?.genres_by_source ?? {}),
    [data?.genres_by_source],
  )

  const seedHref = useMemo(() => {
    const next = new URLSearchParams()
    next.set("seed_id", metadataId)
    next.set("seed_title", title)
    next.set("seed_artist", artist)
    next.set("seed_facets", "genre,bpm,energy")
    return `/playlists?${next.toString()}`
  }, [artist, metadataId, title])

  return (
    <div className="flex flex-col gap-4">
      <Link to="/tracks" className="text-sm text-muted-foreground hover:text-foreground">
        <span className="inline-flex items-center gap-1.5">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to tracks
        </span>
      </Link>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle className="text-2xl">{title}</CardTitle>
            <p className="text-muted-foreground">{artist}</p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => navigate(seedHref)}>
              <Sparkles className="h-3.5 w-3.5" />
              Use as Playlist Seed
            </Button>
            <Button
              variant="secondary"
              onClick={() => play(metadataId, `${artist} - ${title}`)}
            >
              <PlayCircle className="h-3.5 w-3.5" />
              {current?.metadataId === metadataId ? "Stop" : "Play"}
            </Button>
          </div>
        </CardHeader>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <KeyValueCard title="ML Classification" data={data?.ml} />
        <KeyValueCard title="Musical Features" data={data?.musical} />
        <KeyValueCard title="Playlist Signals" data={data?.signals} />
        <KeyValueCard title="Technical" data={data?.tech} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Genres and Tags</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {genreSources.length === 0 ? (
            <p className="text-sm text-muted-foreground">No tags available.</p>
          ) : (
            genreSources.map(([source, values]) => (
              <div key={source} className="space-y-2">
                <p className="text-xs uppercase text-muted-foreground">{source}</p>
                <div className="flex flex-wrap gap-2">
                  {values.map((value, idx) => (
                    <Badge key={`${source}-${idx}`} variant="secondary">
                      {String(value.tag_value ?? "-")}
                    </Badge>
                  ))}
                </div>
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function KeyValueCard({
  title,
  data,
}: {
  title: string
  data: Record<string, unknown> | null | undefined
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {!data ? (
          <p className="text-sm text-muted-foreground">No data.</p>
        ) : (
          <dl className="grid grid-cols-2 gap-2 text-sm">
            {Object.entries(data).map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="text-muted-foreground">{key}</dt>
                <dd className="truncate">{String(value ?? "-")}</dd>
              </div>
            ))}
          </dl>
        )}
      </CardContent>
    </Card>
  )
}
