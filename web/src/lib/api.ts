import type { CheckSource, CheckStatus, EvalReport, Report, Sample } from "./types";

export class ApiError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

/**
 * Plain sentence for a request failure. The server already authors safe
 * messages, so most codes show the message as-is; only lookup failures
 * and rate limits get a local sentence. Never prints a code or a path.
 */
export function userMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "check_not_found" || error.code === "http_404") {
      return "We can't find that report. The link may be wrong, or it was made before the last reset.";
    }
    if (error.code === "queue_full" || error.code === "http_429") {
      return "We're busy right now. Try again in a few minutes.";
    }
    return error.message;
  }
  return "Something went wrong on our side. Please try again.";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let code = `http_${res.status}`;
    let message = res.statusText;
    try {
      const body = (await res.json()) as { error?: { code?: string; message?: string } };
      if (body.error?.code) code = body.error.code;
      if (body.error?.message) message = body.error.message;
    } catch {
      // Keep the HTTP status defaults when the body is not JSON.
    }
    throw new ApiError(code, message);
  }
  return (await res.json()) as T;
}

export async function getHealth(): Promise<{ ok: boolean; version: string }> {
  return request("/api/health");
}

export async function uploadPdf(file: File): Promise<{
  upload_id: string;
  filename: string;
  sha256: string;
}> {
  const form = new FormData();
  form.append("file", file);
  return request("/api/uploads", { method: "POST", body: form });
}

export interface VideoUploadRequest {
  video_upload_id: string;
  video_block_label: string;
  video_same_project: boolean;
}

export async function uploadVideo(file: File): Promise<{
  upload_id: string;
  filename: string;
  sha256: string;
  kind: string;
}> {
  const form = new FormData();
  form.append("file", file);
  return request("/api/uploads", { method: "POST", body: form });
}

export async function uploadPhoto(file: File): Promise<{
  upload_id: string;
  filename: string;
  sha256: string;
  kind: string;
}> {
  const form = new FormData();
  form.append("file", file);
  return request("/api/uploads", { method: "POST", body: form });
}

export async function createCheck(
  projectLabel: string,
  sources: CheckSource[],
  video?: VideoUploadRequest,
  floorsAboveGround?: number,
  photoUploadIds?: string[],
): Promise<{ check_id: string }> {
  return request("/api/checks", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      project_label: projectLabel,
      sources,
      ...video,
      ...(floorsAboveGround === undefined ? {} : { floors_above_ground: floorsAboveGround }),
      ...(photoUploadIds === undefined || photoUploadIds.length === 0
        ? {}
        : { photo_upload_ids: photoUploadIds }),
    }),
  });
}

export async function getStatus(checkId: string): Promise<CheckStatus> {
  return request(`/api/checks/${checkId}`);
}

export async function getReport(checkId: string): Promise<Report> {
  return request(`/api/checks/${checkId}/report`);
}

export async function getSamples(): Promise<Sample[]> {
  return request("/api/samples");
}

export async function getEval(): Promise<EvalReport> {
  return request("/api/eval");
}

/** Build a media URL from a stored artifact name, stripping any directory parts. */
export function mediaUrl(checkId: string, name: string): string {
  const file = name.split("/").pop() ?? "";
  return `/media/${checkId}/${encodeURIComponent(file)}`;
}
