import { createContext, useContext, useMemo, useRef, useState } from "react"
import { Button } from "@/components/ui/button"

type AudioState = {
  metadataId: string
  label: string
}

type AudioContextValue = {
  current: AudioState | null
  play: (metadataId: string, label: string, onEnded?: () => void) => void
  stop: () => void
}

const AudioPlayerContext = createContext<AudioContextValue | null>(null)

export function AudioPlayerProvider({ children }: { children: React.ReactNode }) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [current, setCurrent] = useState<AudioState | null>(null)

  const stop = () => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.currentTime = 0
    }
    setCurrent(null)
  }

  const play = (metadataId: string, label: string, onEnded?: () => void) => {
    if (current?.metadataId === metadataId) {
      stop()
      return
    }
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.currentTime = 0
    }
    const audio = new Audio(`/api/audio/${metadataId}`)
    audioRef.current = audio
    audio.play().catch(() => undefined)
    audio.addEventListener(
      "ended",
      () => {
        setCurrent(null)
        onEnded?.()
      },
      { once: true },
    )
    setCurrent({ metadataId, label })
  }

  const value = useMemo(() => ({ current, play, stop }), [current])

  return (
    <AudioPlayerContext.Provider value={value}>
      {children}
      {current ? (
        <div className="fixed inset-x-0 bottom-0 z-50 border-t bg-card/95 backdrop-blur">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-2">
            <p className="truncate text-sm text-muted-foreground">
              Now playing: <span className="text-foreground">{current.label}</span>
            </p>
            <Button variant="secondary" size="sm" onClick={stop}>
              Stop
            </Button>
          </div>
        </div>
      ) : null}
    </AudioPlayerContext.Provider>
  )
}

export function useAudioPlayer() {
  const context = useContext(AudioPlayerContext)
  if (!context) {
    throw new Error("useAudioPlayer must be used within AudioPlayerProvider")
  }
  return context
}
