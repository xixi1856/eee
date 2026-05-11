import type { NextRequest } from "next/server";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import {
  getThreadMessages,
  softDeleteThread,
  updateThreadTitle,
} from "@/lib/services/chatThreadService";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ sessionId: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { sessionId } = await ctx.params;
    const decoded = decodeURIComponent(sessionId);
    const messages = await getThreadMessages(decoded, auth.sub);
    return jsonOk({ messages });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}

export async function PATCH(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { sessionId } = await ctx.params;
    const decoded = decodeURIComponent(sessionId);
    const body = (await req.json()) as { title?: string };
    const title = typeof body.title === "string" ? body.title : "";
    await updateThreadTitle(decoded, auth.sub, title);
    return jsonOk({ ok: true });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}

export async function DELETE(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { sessionId } = await ctx.params;
    const decoded = decodeURIComponent(sessionId);
    await softDeleteThread(decoded, auth.sub);
    return jsonOk({ ok: true });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
