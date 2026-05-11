import { UserRole } from "@prisma/client";
import type { AuthContext } from "@/lib/middleware-helpers";
import { ApiError } from "@/lib/http/api-error";

/** Secondary ADMIN check for admin Route Handlers (never trust client role alone). */
export function requireAdmin(ctx: AuthContext): void {
  if (ctx.role !== UserRole.ADMIN) {
    throw new ApiError(403, "FORBIDDEN", "ADMIN role required");
  }
}

export function requireAuthenticated(ctx: AuthContext | null): AuthContext {
  if (!ctx) {
    throw new ApiError(401, "UNAUTHORIZED", "Authentication required");
  }
  return ctx;
}

export function parseRoleParam(role: string): UserRole {
  if (role === "STUDENT" || role === "TEACHER" || role === "ADMIN") {
    return role;
  }
  throw new ApiError(400, "VALIDATION_ERROR", "Invalid role");
}
