import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { prisma } from "@/lib/db";
import { enqueueCronTask } from "@/lib/cron/enqueue";
import { computeNextRun } from "@/lib/cron/scheduler";
import { randomUUID } from "crypto";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

/** Check ownership: returns job or throws ApiError */
async function getJobForUser(id: string, userId: string, role: string) {
  const job = await prisma.cronJob.findUnique({ where: { id } });
  if (!job) throw new ApiError(404, "NOT_FOUND", "Cron job not found");
  if (role !== "ADMIN" && job.userId !== userId) {
    throw new ApiError(403, "FORBIDDEN", "Access denied");
  }
  return job;
}

// ---------------------------------------------------------------------------
// GET /api/v1/cron/jobs/[id]
// ---------------------------------------------------------------------------

export async function GET(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { id } = await ctx.params;
    const job = await getJobForUser(id, auth.sub, auth.role);

    const runs = await prisma.cronJobRun.findMany({
      where: { jobId: id },
      orderBy: { startedAt: "desc" },
      take: 10,
      select: {
        id: true,
        status: true,
        startedAt: true,
        finishedAt: true,
        errorMessage: true,
        toolCalls: true,
      },
    });

    return NextResponse.json({ ...job, recentRuns: runs });
  } catch (e) {
    if (e instanceof ApiError) return NextResponse.json(e.toBody(), { status: e.status });
    console.error("[GET /cron/jobs/[id]]", e);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}

// ---------------------------------------------------------------------------
// PATCH /api/v1/cron/jobs/[id]  — pause | resume | trigger
// ---------------------------------------------------------------------------

export async function PATCH(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { id } = await ctx.params;
    const job = await getJobForUser(id, auth.sub, auth.role);

    const body = (await req.json()) as {
      action?: string;
      prompt?: string;
      schedule?: string;
    };

    const action = typeof body.action === "string" ? body.action.trim() : "";

    if (action === "pause") {
      const updated = await prisma.cronJob.update({
        where: { id },
        data: { status: "paused" },
      });
      return NextResponse.json(updated);
    }

    if (action === "resume") {
      const nextRunAt = computeNextRun(job.schedule);
      const updated = await prisma.cronJob.update({
        where: { id },
        data: { status: "active", nextRunAt },
      });
      return NextResponse.json(updated);
    }

    if (action === "trigger") {
      const runId = randomUUID();
      await prisma.cronJobRun.create({ data: { id: runId, jobId: id, status: "running" } });
      await enqueueCronTask({
        run_id: runId,
        job_id: id,
        prompt: job.prompt,
        created_at: new Date().toISOString(),
      });
      return NextResponse.json({ run_id: runId, job_id: id, status: "queued" });
    }

    // Update fields
    const updates: Record<string, unknown> = {};
    if (typeof body.prompt === "string" && body.prompt.trim()) {
      updates.prompt = body.prompt.trim();
    }
    if (typeof body.schedule === "string" && body.schedule.trim()) {
      const newSchedule = body.schedule.trim();
      try {
        updates.nextRunAt = computeNextRun(newSchedule);
      } catch {
        throw new ApiError(400, "VALIDATION_ERROR", `Invalid schedule: "${newSchedule}"`);
      }
      updates.schedule = newSchedule;
    }

    if (Object.keys(updates).length === 0) {
      throw new ApiError(400, "VALIDATION_ERROR", "No valid fields to update");
    }

    const updated = await prisma.cronJob.update({ where: { id }, data: updates });
    return NextResponse.json(updated);
  } catch (e) {
    if (e instanceof ApiError) return NextResponse.json(e.toBody(), { status: e.status });
    console.error("[PATCH /cron/jobs/[id]]", e);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}

// ---------------------------------------------------------------------------
// DELETE /api/v1/cron/jobs/[id]
// ---------------------------------------------------------------------------

export async function DELETE(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { id } = await ctx.params;
    await getJobForUser(id, auth.sub, auth.role);
    await prisma.cronJob.delete({ where: { id } });
    return new NextResponse(null, { status: 204 });
  } catch (e) {
    if (e instanceof ApiError) return NextResponse.json(e.toBody(), { status: e.status });
    console.error("[DELETE /cron/jobs/[id]]", e);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
