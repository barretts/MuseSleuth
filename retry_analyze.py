#!/usr/bin/env python
"""Retry failed analyze jobs."""
from musesleuth.db import get_connection
from musesleuth.job_queue import retry_failed_jobs

conn = get_connection("music.db")
count = retry_failed_jobs(conn, stage="analyze")
print(f"Retried {count} jobs")

print("\nJob counts for analyze stage:")
for row in conn.execute('SELECT status, COUNT(*) FROM jobs WHERE stage="analyze" GROUP BY status'):
    print(f"  {row[0]}: {row[1]}")
