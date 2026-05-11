import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { getAssignment, patchAssignment } from "@/lib/services/assignmentService";
import type { PatchAssignmentBody } from "@/lib/dto/assignment.dto";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string; assignmentId: string }> };

export async function GET(_req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(_req));
    const { courseId, assignmentId } = await ctx.params;
    const assignment = await getAssignment(
      auth.sub,
      auth.role as UserRole,
      courseId,
      assignmentId,
    );
    return jsonOk({ assignment });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(new ApiError(500, "INTERNAL_ERROR", "Internal server error"));
  }
}

export async function PATCH(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId, assignmentId } = await ctx.params;
    const body = (await req.json()) as PatchAssignmentBody;
    const updated = await patchAssignment(
      auth.sub,
      auth.role as UserRole,
      courseId,
      assignmentId,
      body,
    );
    return jsonOk({ assignment: updated });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(new ApiError(500, "INTERNAL_ERROR", "Internal server error"));
  }
}
