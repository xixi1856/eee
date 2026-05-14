import type { NextRequest } from "next/server";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import {
  createEmptyGlobalThread,
  listChatThreads,
} from "@/lib/services/chatThreadService";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const threads = await listChatThreads(auth.sub);
    return jsonOk({ threads });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}

export async function POST(req: NextRequest) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const out = await createEmptyGlobalThread(auth.sub);
    return jsonOk(out);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
