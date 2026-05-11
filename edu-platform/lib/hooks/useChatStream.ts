"use client";

import { useCallback, useRef, useState } from "react";

export type ChatMessage = {
  role: "user" | "assistant";
  text: string;
  attachments?: AttachmentRef[];
};
export type Citation = {
  chunk_id?: string;
  material_id?: string;
  source_label?: string;
};
export type DoneMeta = {
  type: "done";
  tokens?: number;
  exec_time_ms?: number;
  error?: string;
};

export type AttachmentRef = {
  id: string;
  key: string;
  presigned_url: string;
  mime_type: string;
  name: string;
  size: number;
  localPreviewUrl?: string;
};

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

const MAX_FILE_SIZE = 20 * 1024 * 1024;

export function useChatStream(courseId: string) {
  const [msgs, setMsgs] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [lastMeta, setLastMeta] = useState<DoneMeta | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [pendingAttachments, setPendingAttachments] = useState<AttachmentRef[]>([]);
  const [attachmentUploading, setAttachmentUploading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const addAttachment = useCallback(async (file: File) => {
    if (!ALLOWED_MIME_TYPES.has(file.type)) {
      alert(`不支持的文件类型：${file.type || "未知"}`);
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      alert("文件大小不能超过 20 MB");
      return;
    }
    const localPreviewUrl = file.type.startsWith("image/")
      ? URL.createObjectURL(file)
      : undefined;

    setAttachmentUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/v1/attachments", {
        method: "POST",
        credentials: "include",
        body: form,
      });
      if (!res.ok) {
        const j = (await res.json().catch(() => ({}))) as { error?: { message?: string } };
        throw new Error(j.error?.message ?? `上传失败 (${res.status})`);
      }
      const data = (await res.json()) as AttachmentRef;
      setPendingAttachments((prev) => [
        ...prev,
        { ...data, localPreviewUrl },
      ]);
    } catch (e) {
      if (localPreviewUrl) URL.revokeObjectURL(localPreviewUrl);
      alert(e instanceof Error ? e.message : "上传失败");
    } finally {
      setAttachmentUploading(false);
    }
  }, []);

  const removeAttachment = useCallback((id: string) => {
    setPendingAttachments((prev) => {
      const att = prev.find((a) => a.id === id);
      if (att?.localPreviewUrl) URL.revokeObjectURL(att.localPreviewUrl);
      return prev.filter((a) => a.id !== id);
    });
  }, []);

  const sendMessage = useCallback(
    async (text: string, lessonId?: string) => {
      if (busy) return;
      setBusy(true);
      setStreaming("");
      setCitations([]);
      setLastMeta(null);
      setErrorMsg(null);

      // Snapshot and clear pending attachments optimistically
      const snapshotAttachments = pendingAttachments.slice();
      setPendingAttachments([]);

      setMsgs((prev) => [
        ...prev,
        { role: "user", text, attachments: snapshotAttachments.length ? snapshotAttachments : undefined },
      ]);

      abortRef.current?.abort();
      abortRef.current = new AbortController();

      try {
        const attachmentsPayload = snapshotAttachments.map(({ id, key, presigned_url, mime_type, name }) => ({
          id,
          key,
          presigned_url,
          mime_type,
          name,
        }));

        const res = await fetch(`/api/v1/courses/${courseId}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            message: text,
            ...(lessonId ? { lesson_id: lessonId } : {}),
            ...(attachmentsPayload.length ? { attachments: attachmentsPayload } : {}),
          }),
          signal: abortRef.current.signal,
        });

        if (!res.ok) {
          const j = (await res.json().catch(() => ({}))) as {
            error?: { message?: string };
          };
          throw new Error(j.error?.message ?? `请求失败 (${res.status})`);
        }

        const reader = res.body?.getReader();
        if (!reader) throw new Error("无法读取响应流");

        const decoder = new TextDecoder();
        let buffer = "";
        let streamText = "";
        const newCitations: Citation[] = [];

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split("\n\n");
          buffer = parts.pop() ?? "";

          for (const part of parts) {
            if (!part.startsWith("data: ")) continue;
            const raw = part.slice(6).trim();
            if (!raw) continue;
            try {
              const event = JSON.parse(raw) as {
                type: string;
                content?: string;
                chunk_id?: string;
                material_id?: string;
                source_label?: string;
                tokens?: number;
                exec_time_ms?: number;
                error?: string;
              };

              if (event.type === "text" && event.content) {
                streamText += event.content;
                setStreaming(streamText);
              } else if (event.type === "citation") {
                newCitations.push({
                  chunk_id: event.chunk_id,
                  material_id: event.material_id,
                  source_label: event.source_label,
                });
                setCitations([...newCitations]);
              } else if (event.type === "done") {
                const meta: DoneMeta = {
                  type: "done",
                  tokens: event.tokens,
                  exec_time_ms: event.exec_time_ms,
                  error: event.error,
                };
                setLastMeta(meta);
                if (event.error) setErrorMsg(event.error);
              }
            } catch {
              // Skip malformed SSE events
            }
          }
        }

        if (streamText) {
          setMsgs((prev) => [...prev, { role: "assistant", text: streamText }]);
        }
        setStreaming("");
      } catch (e) {
        if ((e as { name?: string }).name !== "AbortError") {
          const msg = e instanceof Error ? e.message : "请求失败";
          setErrorMsg(msg);
          setMsgs((prev) => [
            ...prev,
            { role: "assistant", text: `[错误: ${msg}]` },
          ]);
        }
        setStreaming("");
      } finally {
        setBusy(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [courseId, busy, pendingAttachments]
  );

  return {
    msgs,
    streaming,
    busy,
    citations,
    lastMeta,
    errorMsg,
    sendMessage,
    pendingAttachments,
    attachmentUploading,
    addAttachment,
    removeAttachment,
  };
}
