import { NextResponse } from "next/server";
import { ApiError } from "@/lib/http/api-error";

export function jsonError(err: ApiError): NextResponse {
  return NextResponse.json(err.toBody(), { status: err.status });
}

export function jsonOk(data: unknown, status = 200): NextResponse {
  return NextResponse.json(data, { status });
}
