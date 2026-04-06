import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import { AppShell } from "@/components/app-shell"
import { AudioPlayerProvider } from "@/components/audio-player"
import { TooltipProvider } from "@/components/ui/tooltip"
import { PlaylistDetailPage } from "@/pages/playlist-detail-page"
import { PlaylistsPage } from "@/pages/playlists-page"
import { SpotifyImportPage } from "@/pages/spotify-import-page"
import { StatusPage } from "@/pages/status-page"
import { TrackDetailPage } from "@/pages/track-detail-page"
import { TracksPage } from "@/pages/tracks-page"

function App() {
  return (
    <TooltipProvider>
      <AudioPlayerProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<Navigate to="/tracks" replace />} />
              <Route path="/tracks" element={<TracksPage />} />
              <Route path="/tracks/:metadataId" element={<TrackDetailPage />} />
              <Route path="/playlists" element={<PlaylistsPage />} />
              <Route path="/playlists/import-spotify" element={<SpotifyImportPage />} />
              <Route path="/playlists/:playlistId" element={<PlaylistDetailPage />} />
              <Route path="/status" element={<StatusPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AudioPlayerProvider>
    </TooltipProvider>
  )
}

export default App
