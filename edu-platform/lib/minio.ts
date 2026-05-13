import {
  DeleteObjectCommand,
  GetObjectCommand,
  HeadObjectCommand,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import type { Readable } from "node:stream";
import { getMinioConfig } from "@/lib/config";

function buildClient(): S3Client {
  const c = getMinioConfig();
  const endpointUrl = c.endpoint.startsWith("http")
    ? c.endpoint
    : c.useSsl
      ? `https://${c.endpoint}`
      : `http://${c.endpoint}`;
  return new S3Client({
    region: c.region,
    endpoint: endpointUrl,
    forcePathStyle: true,
    credentials: {
      accessKeyId: c.accessKeyId,
      secretAccessKey: c.secretAccessKey,
    },
  });
}

let _client: S3Client | null = null;

export function getS3Client(): S3Client {
  if (!_client) _client = buildClient();
  return _client;
}

export async function putObjectStream(params: {
  objectKey: string;
  body: Readable;
  contentLength?: number;
  contentType?: string;
}): Promise<void> {
  const c = getMinioConfig();
  const client = getS3Client();
  await client.send(
    new PutObjectCommand({
      Bucket: c.bucket,
      Key: params.objectKey,
      Body: params.body,
      ContentLength: params.contentLength,
      ContentType: params.contentType,
    }),
  );
}

export async function deleteObject(objectKey: string): Promise<void> {
  const c = getMinioConfig();
  const client = getS3Client();
  await client.send(
    new DeleteObjectCommand({ Bucket: c.bucket, Key: objectKey }),
  );
}

export async function objectExists(objectKey: string): Promise<boolean> {
  const c = getMinioConfig();
  const client = getS3Client();
  try {
    await client.send(
      new HeadObjectCommand({ Bucket: c.bucket, Key: objectKey }),
    );
    return true;
  } catch (e) {
    const err = e as {
      name?: string;
      code?: string;
      Code?: string;
      $metadata?: { httpStatusCode?: number };
    };
    const status = Number(err.$metadata?.httpStatusCode ?? 0);
    const code = String(err.code ?? err.Code ?? err.name ?? "");
    if (status === 404 || code === "NotFound" || code === "NoSuchKey") {
      return false;
    }
    throw e;
  }
}

export async function getObjectStream(params: {
  objectKey: string;
}): Promise<{
  body: BodyInit;
  contentType: string | undefined;
  contentLength: number | undefined;
}> {
  const c = getMinioConfig();
  const client = getS3Client();
  const res = await client.send(
    new GetObjectCommand({ Bucket: c.bucket, Key: params.objectKey }),
  );
  if (!res.Body) {
    throw new Error("S3 GetObject returned empty body");
  }
  return {
    body: res.Body.transformToWebStream() as unknown as BodyInit,
    contentType: res.ContentType,
    contentLength:
      typeof res.ContentLength === "number" ? res.ContentLength : undefined,
  };
}
