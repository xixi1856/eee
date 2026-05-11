import type { NextRequest } from "next/server";
import { MaterialStatus, UserRole } from "@prisma/client";
import { Readable } from "node:stream";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import {
  listMaterials,
  uploadMaterialStream,
} from "@/lib/services/materialService";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId } = await ctx.params;
    const statusParam = req.nextUrl.searchParams.get("status");
    const filters: { status?: MaterialStatus } = {};
    if (statusParam) {
      const allowed = new Set<string>(Object.values(MaterialStatus));
      if (!allowed.has(statusParam)) {
        throw new ApiError(400, "VALIDATION_ERROR", "Invalid status filter");
      }
      filters.status = statusParam as MaterialStatus;
    }
    const out = await listMaterials(auth.sub, auth.role as UserRole, courseId, filters);
    return jsonOk(out);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}

export async function POST(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId } = await ctx.params;
    const form = await req.formData();
    const file = form.get("file");
    if (!(file instanceof File)) {
      throw new ApiError(400, "VALIDATION_ERROR", "multipart field 'file' is required");
    }
    const lessonRaw = form.get("lesson_id");
    const lessonId =
      typeof lessonRaw === "string" && lessonRaw.trim() ? lessonRaw.trim() : null;
    const size = file.size;
    if (size <= 0) {
      throw new ApiError(400, "VALIDATION_ERROR", "Empty file");
    }
    const body = Readable.fromWeb(file.stream() as import("node:stream/web").ReadableStream);
    const created = await uploadMaterialStream({
      teacherUserId: auth.sub,
      role: auth.role as UserRole,
      courseId,
      originalFilename: file.name || "upload.bin",
      contentType: file.type || undefined,
      contentLength: size,
      body,
      lessonId,
    });
    return jsonOk(created, 201);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
