import { useEffect, useState } from "react"
import { api } from "@/lib/api"
import type { StatusResponse } from "@/lib/types"
import { Card, CardContent } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

export function StatusPage() {
  const [data, setData] = useState<StatusResponse | null>(null)

  useEffect(() => {
    api.getStatus().then(setData)
  }, [])

  if (!data) return <p>Loading...</p>

  const summary = [
    ["Tracks", data.stats.total_tracks, "text-foreground"],
    ["Pending", data.stats.pending_jobs, "text-yellow-400"],
    ["Running", data.stats.running_jobs, "text-blue-400"],
    ["Done", data.stats.done_jobs, "text-green-400"],
    ["Failed", data.stats.failed_jobs, "text-red-400"],
  ] as const

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold">Pipeline Status</h1>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {summary.map(([label, value, cls]) => (
          <Card key={label}>
            <CardContent className="p-4 text-center">
              <p className="text-xs uppercase text-muted-foreground">{label}</p>
              <p className={`text-2xl font-bold ${cls}`}>{value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardContent className="space-y-2 p-4">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Completion</span>
            <span className="font-semibold">{data.stats.completion_pct.toFixed(1)}%</span>
          </div>
          <Progress value={data.stats.completion_pct} />
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Stage</TableHead>
                <TableHead className="text-center">Pending</TableHead>
                <TableHead className="text-center">Running</TableHead>
                <TableHead className="text-center">Done</TableHead>
                <TableHead className="text-center">Failed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.stages.map((stage) => (
                <TableRow key={stage.name} className="odd:bg-muted/20 even:bg-background hover:bg-muted/40">
                  <TableCell className="font-medium">{stage.name}</TableCell>
                  <TableCell className="text-center text-yellow-400">{stage.pending || "-"}</TableCell>
                  <TableCell className="text-center text-blue-400">{stage.running || "-"}</TableCell>
                  <TableCell className="text-center text-green-400">{stage.done || "-"}</TableCell>
                  <TableCell className="text-center text-red-400">{stage.failed || "-"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
