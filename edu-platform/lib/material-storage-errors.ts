import { S3ServiceException } from "@aws-sdk/client-s3";
import { ApiError } from "@/lib/http/api-error";

/** Map MinIO/S3 read failures to API errors for material content routes. */
export function mapStorageReadError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;

  if (err instanceof S3ServiceException) {
    const code = err.name;
    const status = err.$metadata?.httpStatusCode;
    if (code === "NoSuchKey" || status === 404) {
      return new ApiError(404, "NOT_FOUND", "File not found in object storage");
    }
    if (code === "AccessDenied" || status === 403) {
      return new ApiError(403, "FORBIDDEN", "Access denied for object storage");
    }
  }

  const msg = err instanceof Error ? err.message : String(err);
  const lower = msg.toLowerCase();
  if (lower.includes("timeout") || lower.includes("etimedout")) {
    return new ApiError(
      503,
      "SERVICE_UNAVAILABLE",
      "Object storage request timed out",
      { detail: msg.slice(0, 500) },
    );
  }

  return new ApiError(
    503,
    "SERVICE_UNAVAILABLE",
    "Object storage read failed",
    { detail: msg.slice(0, 500) },
  );
}
