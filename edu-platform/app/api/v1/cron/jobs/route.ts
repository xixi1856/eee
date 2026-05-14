import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { jsonOk } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { prisma } from "@/lib/db";
import { randomUUID } from "crypto";
import { computeNextRun } from "@/lib/cron/scheduler";

const e500 = () => NextResponse.json({ error: "Internal server error" }, { status: 500 });

export const dynamic = "force-dynamic";

// ---------------------------------------------------------------------------
// GET /api/v1/cron/jobs  — list jobs owned by current user (ADMIN sees all)
// ---------------------------------------------------------------------------

export async function GET(req: NextRequest) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const isAdmin = auth.role === "ADMIN";

    const jobs = await prisma.cronJob.findMany({
      where: isAdmin ? {} : { userId: auth.sub },
      orderBy: { createdAt: "desc" },
      include: {
        _count: { select: { runs: true } },
        runs: {
          orderBy: { startedAt: "desc" },
          take: 1,
          select: { status: true, startedAt: true, finishedAt: true },
        },
      },
    });

    return jsonOk(jobs);
  } catch (e) {
    if (e instanceof ApiError) return NextResponse.json(e.toBody(), { status: e.status });
    console.error("[GET /cron/jobs]", e);
    return e500();
  }
}

// ---------------------------------------------------------------------------
// POST /api/v1/cron/jobs  — create a new cron job
// ---------------------------------------------------------------------------

export async function POST(req: NextRequest) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const body = (await req.json()) as {
      prompt?: string;
      schedule?: string;
    };

    const prompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
    const schedule = typeof body.schedule === "string" ? body.schedule.trim() : "";

    if (!prompt) throw new ApiError(400, "VALIDATION_ERROR", "prompt is required");
    if (!schedule) throw new ApiError(400, "VALIDATION_ERROR", "schedule is required");

    // Validate schedule by attempting to compute next run
    let nextRunAt: Date;
    try {
      nextRunAt = computeNextRun(schedule);
    } catch {
      throw new ApiError(400, "VALIDATION_ERROR", `Invalid schedule expression: "${schedule}"`);
    }

    const id = randomUUID().replace(/-/g, "").slice(0, 16);
    const job = await prisma.cronJob.create({
      data: {
        id,
        userId: auth.sub,
        prompt,
        schedule,
        status: "active",
        nextRunAt,
      },
    });

    return jsonOk(job, 201);
  } catch (e) {
    if (e instanceof ApiError) return NextResponse.json(e.toBody(), { status: e.status });
    console.error("[POST /cron/jobs]", e);
    return e500();
  }
}
