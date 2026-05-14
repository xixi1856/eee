/**
 * Cron scheduler — runs inside the Next.js server process.
 * Every 60 seconds it checks for due CronJobs and enqueues them to Redis Stream.
 * Worker (scripts/cron-worker.ts) consumes the stream and runs the agent.
 */

import { randomUUID } from "crypto";
import { prisma } from "@/lib/db";
import { enqueueCronTask } from "@/lib/cron/enqueue";

// ---------------------------------------------------------------------------
// Schedule parsing
// ---------------------------------------------------------------------------

const _EVERY_RE = /^every\s+(\d+)\s*(m|min|minute|h|hour|d|day)s?$/i;

function _parseIntervalMs(schedule: string): number | null {
  const m = _EVERY_RE.exec(schedule.trim());
  if (!m) return null;
  const n = parseInt(m[1], 10);
  const unit = m[2].toLowerCase();
  if (unit === "m" || unit === "min" || unit === "minute") return n * 60_000;
  if (unit === "h" || unit === "hour") return n * 3_600_000;
  if (unit === "d" || unit === "day") return n * 86_400_000;
  return null;
}

/** Parse a 5-field cron expression and return the next Date after `after`. */
export function cronNextAfter(expr: string, after: Date): Date {
  // Support "HH:MM" shorthand → convert to "MM HH * * *"
  const hmMatch = /^(\d{1,2}):(\d{2})$/.exec(expr.trim());
  if (hmMatch) {
    expr = `${hmMatch[2]} ${hmMatch[1]} * * *`;
  }

  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) throw new Error(`Invalid cron expression: ${expr}`);
  const [minF, hourF, , , ] = parts;

  // Simple implementation: only supports wildcards and integers
  const parseField = (f: string, max: number): number[] => {
    if (f === "*") return Array.from({ length: max }, (_, i) => i);
    const n = parseInt(f, 10);
    if (Number.isNaN(n)) throw new Error(`Unsupported cron field: ${f}`);
    return [n];
  };

  const validMins = parseField(minF, 60);
  const validHours = parseField(hourF, 24);

  // Find next matching minute/hour from `after` (search up to 48 hours ahead)
  const candidate = new Date(after.getTime() + 60_000); // at least 1 minute ahead
  candidate.setSeconds(0, 0);

  for (let h = 0; h < 48 * 60; h++) {
    const hr = candidate.getHours();
    const mn = candidate.getMinutes();
    if (validHours.includes(hr) && validMins.includes(mn)) {
      return new Date(candidate);
    }
    candidate.setTime(candidate.getTime() + 60_000);
  }
  throw new Error(`Cannot compute next run for cron: ${expr}`);
}

export function computeNextRun(schedule: string, after: Date = new Date()): Date {
  const intervalMs = _parseIntervalMs(schedule);
  if (intervalMs !== null) {
    return new Date(after.getTime() + intervalMs);
  }
  return cronNextAfter(schedule, after);
}

// ---------------------------------------------------------------------------
// Main scheduler tick
// ---------------------------------------------------------------------------

async function _tick(): Promise<void> {
  const now = new Date();
  let dueJobs: { id: string; prompt: string; schedule: string }[];
  try {
    dueJobs = await prisma.cronJob.findMany({
      where: { status: "active", nextRunAt: { lte: now } },
      select: { id: true, prompt: true, schedule: true },
    });
  } catch (err) {
    console.error("[CronScheduler] DB query failed:", err);
    return;
  }

  for (const job of dueJobs) {
    const runId = randomUUID();
    try {
      // Create the run record
      await prisma.cronJobRun.create({
        data: { id: runId, jobId: job.id, status: "running" },
      });
      // Enqueue to Redis Stream
      await enqueueCronTask({
        run_id: runId,
        job_id: job.id,
        prompt: job.prompt,
        created_at: now.toISOString(),
      });
      // Update job timestamps
      const nextRunAt = computeNextRun(job.schedule, now);
      await prisma.cronJob.update({
        where: { id: job.id },
        data: { lastRunAt: now, nextRunAt },
      });
    } catch (err) {
      console.error(`[CronScheduler] Failed to enqueue job ${job.id}:`, err);
      // Mark run as failed if it was created
      try {
        await prisma.cronJobRun.update({
          where: { id: runId },
          data: {
            status: "failed",
            errorMessage: String(err),
            finishedAt: new Date(),
          },
        });
      } catch {
        // best-effort
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Exported startup function
// ---------------------------------------------------------------------------

export function startCronScheduler(): void {
  // Initial tick after 10 s to let DB connections settle
  const initialTimer = setTimeout(() => {
    void _tick();
    const interval = setInterval(() => void _tick(), 60_000);
    // Expose handle for graceful shutdown in tests
    if (typeof globalThis !== "undefined") {
      (globalThis as Record<string, unknown>).__cronIntervalHandle = interval;
    }
  }, 10_000);

  if (typeof globalThis !== "undefined") {
    (globalThis as Record<string, unknown>).__cronInitialTimer = initialTimer;
  }
}
