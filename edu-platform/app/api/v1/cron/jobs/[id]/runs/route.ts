import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { prisma } from "@/lib/db";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

// ---------------------------------------------------------------------------
// GET /api/v1/cron/jobs/[id]/runs  — paginated run history
// ---------------------------------------------------------------------------

export async function GET(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { id } = await ctx.params;

    const job = await prisma.cronJob.findUnique({ where: { id }, select: { userId: true } });
    if (!job) throw new ApiError(404, "NOT_FOUND", "Cron job not found");
    if (auth.role !== "ADMIN" && job.userId !== auth.sub) {
      throw new ApiError(403, "FORBIDDEN", "Access denied");
    }

    const { searchParams } = new URL(req.url);
    const take = Math.min(parseInt(searchParams.get("limit") ?? "20", 10), 100);
    const cursor = searchParams.get("cursor") ?? undefined;

    const runs = await prisma.cronJobRun.findMany({
      where: { jobId: id },
      orderBy: { startedAt: "desc" },
      take: take + 1,
      ...(cursor ? { cursor: { id: cursor }, skip: 1 } : {}),
      select: {
        id: true,
        status: true,
        output: true,
        toolCalls: true,
        startedAt: true,
        finishedAt: true,
        errorMessage: true,
      },
    });

    const hasMore = runs.length > take;
    const items = hasMore ? runs.slice(0, take) : runs;
    const nextCursor = hasMore ? items[items.length - 1]?.id : null;

    return NextResponse.json({ items, nextCursor, hasMore });
  } catch (e) {
    if (e instanceof ApiError) return NextResponse.json(e.toBody(), { status: e.status });
    console.error("[GET /cron/jobs/[id]/runs]", e);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
