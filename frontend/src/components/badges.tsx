import { Badge } from "@/components/ui/badge"

export function CamelotBadge({ value }: { value?: string | null }) {
  if (!value) return <span>-</span>
  return <Badge variant="secondary">{value}</Badge>
}

export function GenreBadge({ value }: { value?: string | null }) {
  if (!value) return <span>-</span>
  return <Badge variant="secondary">{value}</Badge>
}

export function EnergyBadge({ value }: { value?: string | null }) {
  if (!value) return <span>-</span>
  return <Badge variant="outline">{value}</Badge>
}
