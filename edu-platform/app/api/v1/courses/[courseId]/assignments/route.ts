import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { listAssignments, triggerAssignmentGeneration } from "@/lib/services/assignmentService";
import type { GenerateAssignmentBody } from "@/lib/dto/assignment.dto";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string }> };

export async function GET(_req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(_req));
    const { courseId } = await ctx.params;
    const assignments = await listAssignments(auth.sub, auth.role as UserRole, courseId);
    return jsonOk({ assignments });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(new ApiError(500, "INTERNAL_ERROR", "Internal server error"));
  }
}

export async function POST(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId } = await ctx.params;
    const body = (await req.json()) as GenerateAssignmentBody;
    const assignment = await triggerAssignmentGeneration(
      auth.sub,
      auth.role as UserRole,
      courseId,
      body,
    );
    return jsonOk({ assignment }, 202);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(new ApiError(500, "INTERNAL_ERROR", "Internal server error"));
  }
}
