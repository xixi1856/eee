import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { joinCourseByShareCode } from "@/lib/services/courseService";
import type { JoinByShareCodeBody } from "@/lib/dto/course.dto";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const body = (await req.json()) as JoinByShareCodeBody;
    const out = await joinCourseByShareCode(
      auth.sub,
      auth.role as UserRole,
      body?.share_code ?? "",
    );
    return jsonOk(out, 201);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
