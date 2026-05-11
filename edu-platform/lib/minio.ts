import {
  DeleteObjectCommand,
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
