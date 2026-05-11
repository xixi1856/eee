import type { NextRequest } from "next/server";
import { GetObjectCommand, PutObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { getS3Client } from "@/lib/minio";
import { getMinioConfig } from "@/lib/config";
import { ApiError } from "@/lib/http/api-error";
import { jsonError, jsonOk } from "@/lib/http/json-response";

export const dynamic = "force-dynamic";

const ALLOWED_MIME_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/gif",
  "image/webp",
  "application/pdf",
  "text/plain",
  "text/markdown",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.ms-powerpoint",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
]);

const MAX_FILE_SIZE = 20 * 1024 * 1024; // 20 MB
const PRESIGN_TTL_SECONDS = 3600; // 1 hour

function sanitizeFilename(name: string): string {
  return name.replace(/[^a-zA-Z0-9._\-\u4e00-\u9fa5]/g, "_").slice(0, 200);
}

export async function POST(req: NextRequest) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));

    let formData: FormData;
    try {
      formData = await req.formData();
    } catch {
      throw new ApiError(400, "VALIDATION_ERROR", "Expected multipart/form-data");
    }

    const file = formData.get("file");
    if (!(file instanceof File)) {
      throw new ApiError(400, "VALIDATION_ERROR", "Missing 'file' field");
    }

    if (file.size === 0) {
      throw new ApiError(400, "VALIDATION_ERROR", "File is empty");
    }
    if (file.size > MAX_FILE_SIZE) {
      throw new ApiError(413, "FILE_TOO_LARGE", `File exceeds 20 MB limit`);
    }

    const mimeType = file.type || "application/octet-stream";
    if (!ALLOWED_MIME_TYPES.has(mimeType)) {
      throw new ApiError(415, "UNSUPPORTED_MEDIA_TYPE", `File type '${mimeType}' is not allowed`);
    }

    const id = crypto.randomUUID();
    const safeName = sanitizeFilename(file.name || "attachment");
    const objectKey = `tmp-attachments/${auth.sub}/${id}-${safeName}`;

    const buffer = Buffer.from(await file.arrayBuffer());

    const c = getMinioConfig();
    const client = getS3Client();

    await client.send(
      new PutObjectCommand({
        Bucket: c.bucket,
        Key: objectKey,
        Body: buffer,
        ContentType: mimeType,
        ContentLength: buffer.byteLength,
      }),
    );

    const presignedUrl = await getSignedUrl(
      client,
      new GetObjectCommand({ Bucket: c.bucket, Key: objectKey }),
      { expiresIn: PRESIGN_TTL_SECONDS },
    );

    return jsonOk({
      id,
      key: objectKey,
      presigned_url: presignedUrl,
      mime_type: mimeType,
      name: file.name || safeName,
      size: file.size,
    });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(new ApiError(500, "INTERNAL_ERROR", "Internal server error"));
  }
}
