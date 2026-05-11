import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { regenerateQuestion } from "@/lib/services/assignmentService";
import type { RegenerateQuestionBody } from "@/lib/dto/assignment.dto";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string; assignmentId: string }> };

export async function POST(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId, assignmentId } = await ctx.params;
    const body = (await req.json()) as RegenerateQuestionBody;
    const question = await regenerateQuestion(
      auth.sub,
      auth.role as UserRole,
      courseId,
      assignmentId,
      body,
    );
    return jsonOk({ question });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(new ApiError(500, "INTERNAL_ERROR", "Internal server error"));
  }
}
