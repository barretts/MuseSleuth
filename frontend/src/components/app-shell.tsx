import { NavLink, Outlet } from "react-router-dom"
import { Activity, ListMusic, Music2 } from "lucide-react"

const links = [
  { to: "/tracks", label: "Tracks", icon: Music2 },
  { to: "/playlists", label: "Playlists", icon: ListMusic },
  { to: "/status", label: "Pipeline", icon: Activity },
]

export function AppShell() {
  return (
    <div className="dark min-h-screen bg-background text-foreground">
      <nav className="border-b bg-card">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-8 px-4">
          <NavLink to="/tracks" className="text-lg font-bold text-foreground">
            MuseSleuth
          </NavLink>
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              className={({ isActive }) =>
                `text-sm transition ${
                  isActive
                    ? "font-medium text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`
              }
            >
              <span className="inline-flex items-center gap-1.5">
                <link.icon className="h-3.5 w-3.5" />
                {link.label}
              </span>
            </NavLink>
          ))}
        </div>
      </nav>
      <main className="mx-auto w-full max-w-7xl px-4 py-6 pb-20">
        <Outlet />
      </main>
    </div>
  )
}
