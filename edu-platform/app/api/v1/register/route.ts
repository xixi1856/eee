import { NextResponse, type NextRequest } from "next/server";
import { registerUser } from "@/lib/services/authService";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { parseRoleParam } from "@/lib/admin";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  try {
    const body = (await req.json()) as {
      username?: string;
      email?: string;
      password?: string;
      role?: string;
    };
    if (
      typeof body.username !== "string" ||
      typeof body.email !== "string" ||
      typeof body.password !== "string" ||
      typeof body.role !== "string"
    ) {
      throw new ApiError(400, "VALIDATION_ERROR", "Invalid request body");
    }
    const role = parseRoleParam(body.role);
    const result = await registerUser({
      username: body.username,
      email: body.email,
      password: body.password,
      role,
    });
    return jsonOk(result, 201);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
