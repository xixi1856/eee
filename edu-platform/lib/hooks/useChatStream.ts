"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type ChatMessage = {
  clientId: string;
  role: "user" | "assistant";
  text: string;
  attachments?: AttachmentRef[];
  /** Same QaLog row id for hydrated user/assistant pair */
  qaLogId?: string;
  /** After editing this user turn, the assistant reply that was replaced (client-only). */
  supersededAssistantReply?: string;
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

export type UseChatStreamConfig =
  | {
      kind: "course";
      courseId: string;
      /** When set, load historical Q/A from QA center API into the transcript. */
      hydrateSessionId?: string | null;
    }
  | {
      kind: "qa_center_global";
      sessionId: string | null;
      onResolvedSessionId?: (id: string) => void;
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

type ApiErrJson = {
  error?: { message?: string; code?: string };
};

function formatChatHttpError(status: number, body: ApiErrJson): string {
  const raw = (body.error?.message ?? "").trim();
  const code = body.error?.code;
  const vague = !raw || raw === "Internal server error";
  if (vague && code === "AGENT_UNAVAILABLE") {
    return "EduAgent 网关不可用：请确认已在仓库根启动 uv run edu-gateway，且 EDU_AGENT_BASE_URL 与网关地址一致。";
  }
  if (vague && code === "AGENT_NOT_BOUND") {
    return "尚未绑定 Agent 身份：请完成 edu bind 后，在本平台「凭证」页（/credentials）完成关联。";
  }
  return raw || `请求失败 (${status})`;
}

function newClientId(): string {
  return crypto.randomUUID();
}

function isAssistantErrorBubble(text: string): boolean {
  return text.startsWith("[错误:");
}

type HydratedRow = {
  id?: string;
  question: string;
  answer: string | null;
};

function logToMsgs(rows: HydratedRow[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const r of rows) {
    const qaLogId = typeof r.id === "string" && r.id ? r.id : undefined;
    out.push({
      clientId: newClientId(),
      role: "user",
      text: r.question,
      ...(qaLogId ? { qaLogId } : {}),
    });
    if (r.answer) {
      out.push({
        clientId: newClientId(),
        role: "assistant",
        text: r.answer,
        ...(qaLogId ? { qaLogId } : {}),
      });
    }
  }
  return out;
}

function mapAttachmentsForPayload(refs: AttachmentRef[]) {
  return refs.map(({ id, key, presigned_url, mime_type, name }) => ({
    id,
    key,
    presigned_url,
    mime_type,
    name,
  }));
}

export function useChatStream(config: UseChatStreamConfig) {
  const cfgRef = useRef(config);
  cfgRef.current = config;
  const [msgs, setMsgs] = useState<ChatMessage[]>([]);
  const msgsRef = useRef<ChatMessage[]>([]);
  msgsRef.current = msgs;

  const [streaming, setStreaming] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [lastMeta, setLastMeta] = useState<DoneMeta | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [pendingAttachments, setPendingAttachments] = useState<AttachmentRef[]>([]);
  const [attachmentUploading, setAttachmentUploading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const historyLoadKey =
    config.kind === "qa_center_global"
      ? `q:${config.sessionId ?? ""}`
      : `c:${config.courseId}:h:${config.hydrateSessionId ?? ""}`;

  useEffect(() => {
    const c = cfgRef.current;
    if (c.kind === "qa_center_global") {
      if (!c.sessionId) {
        setMsgs([]);
        return;
      }
      const sid = c.sessionId;
      let cancelled = false;
      void (async () => {
        try {
          const res = await fetch(
            `/api/v1/me/chat-threads/${encodeURIComponent(sid)}`,
            { credentials: "include" },
          );
          if (!res.ok || cancelled) return;
          const body = (await res.json()) as {
            messages?: HydratedRow[];
          };
          const rows = Array.isArray(body.messages) ? body.messages : [];
          if (!cancelled) setMsgs(logToMsgs(rows));
        } catch {
          if (!cancelled) setMsgs([]);
        }
      })();
      return () => {
        cancelled = true;
      };
    }
    if (c.kind === "course") {
      if (!c.hydrateSessionId) return;
      const sid = c.hydrateSessionId;
      let cancelled = false;
      void (async () => {
        try {
          const res = await fetch(
            `/api/v1/me/chat-threads/${encodeURIComponent(sid)}`,
            { credentials: "include" },
          );
          if (!res.ok || cancelled) return;
          const body = (await res.json()) as {
            messages?: HydratedRow[];
          };
          const rows = Array.isArray(body.messages) ? body.messages : [];
          if (!cancelled) setMsgs(logToMsgs(rows));
        } catch {
          if (!cancelled) setMsgs([]);
        }
      })();
      return () => {
        cancelled = true;
      };
    }
  }, [historyLoadKey]);

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

  const runChatRequest = useCallback(
    async (message: string, lessonId: string | undefined, attachmentRefs: AttachmentRef[]) => {
      const config = cfgRef.current;
      setBusy(true);
      setStreaming("");
      setCitations([]);
      setLastMeta(null);
      setErrorMsg(null);

      abortRef.current?.abort();
      abortRef.current = new AbortController();

      const attachmentsPayload = mapAttachmentsForPayload(attachmentRefs);

      try {
        const isQaGlobal = config.kind === "qa_center_global";
        const courseId = config.kind === "course" ? config.courseId : "";
        const url = isQaGlobal
          ? "/api/v1/qa-center/chat"
          : `/api/v1/courses/${courseId}/chat`;

        const body: Record<string, unknown> = {
          message,
          ...(lessonId ? { lesson_id: lessonId } : {}),
          ...(attachmentsPayload.length ? { attachments: attachmentsPayload } : {}),
        };
        if (isQaGlobal && config.sessionId) {
          body.session_id = config.sessionId;
        }

        const res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify(body),
          signal: abortRef.current.signal,
        });

        if (isQaGlobal) {
          const hdr = res.headers.get("X-Qa-Center-Session-Id");
          if (hdr && config.onResolvedSessionId) {
            config.onResolvedSessionId(hdr);
          }
        }

        if (!res.ok) {
          const j = (await res.json().catch(() => ({}))) as ApiErrJson;
          throw new Error(formatChatHttpError(res.status, j));
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
          const assistantMsg: ChatMessage = {
            clientId: newClientId(),
            role: "assistant",
            text: streamText,
          };
          setMsgs((prev) => {
            const next = [...prev, assistantMsg];
            msgsRef.current = next;
            return next;
          });
        }
        setStreaming("");
      } catch (e) {
        if ((e as { name?: string }).name !== "AbortError") {
          const msg = e instanceof Error ? e.message : "请求失败";
          setErrorMsg(msg);
          const errBubble: ChatMessage = {
            clientId: newClientId(),
            role: "assistant",
            text: `[错误: ${msg}]`,
          };
          setMsgs((prev) => {
            const next = [...prev, errBubble];
            msgsRef.current = next;
            return next;
          });
        }
        setStreaming("");
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const sendMessage = useCallback(
    async (text: string, lessonId?: string) => {
      if (busy) return;
      const trimmed = text.trim();
      if (!trimmed && pendingAttachments.length === 0) return;

      const snapshotAttachments = pendingAttachments.slice();
      setPendingAttachments([]);

      const userMsg: ChatMessage = {
        clientId: newClientId(),
        role: "user",
        text: trimmed,
        ...(snapshotAttachments.length ? { attachments: snapshotAttachments } : {}),
      };

      setMsgs((prev) => {
        const next = [...prev, userMsg];
        msgsRef.current = next;
        return next;
      });

      await runChatRequest(trimmed, lessonId, snapshotAttachments);
    },
    [busy, pendingAttachments, runChatRequest],
  );

  const commitUserEditReplace = useCallback(
    async (replaceFromIndex: number, text: string, lessonId?: string): Promise<boolean> => {
      if (busy) return false;
      const trimmed = text.trim();
      const prev = msgsRef.current;
      const row = prev[replaceFromIndex];
      if (!row || row.role !== "user") return false;
      const hasAtt = (row.attachments?.length ?? 0) > 0;
      if (!trimmed && !hasAtt) return false;

      const nextAssistant = prev[replaceFromIndex + 1];
      let superseded: string | undefined;
      if (
        nextAssistant?.role === "assistant" &&
        !isAssistantErrorBubble(nextAssistant.text)
      ) {
        superseded = nextAssistant.text;
      }

      const attachments = row.attachments ?? [];
      const newUser: ChatMessage = {
        clientId: newClientId(),
        role: "user",
        text: trimmed,
        ...(attachments.length ? { attachments } : {}),
        ...(superseded !== undefined ? { supersededAssistantReply: superseded } : {}),
      };

      const messageForApi = trimmed;

      const newMsgs = [...prev.slice(0, replaceFromIndex), newUser];
      msgsRef.current = newMsgs;
      setMsgs(newMsgs);

      await runChatRequest(messageForApi, lessonId, attachments);
      return true;
    },
    [busy, runChatRequest],
  );

  const regenerateAssistantAt = useCallback(
    async (assistantIndex: number, lessonId?: string) => {
      if (busy) return;
      const prev = msgsRef.current;
      const userRow = prev[assistantIndex - 1];
      const assistantRow = prev[assistantIndex];
      if (
        !userRow ||
        userRow.role !== "user" ||
        !assistantRow ||
        assistantRow.role !== "assistant"
      ) {
        return;
      }

      const newMsgs = prev.slice(0, assistantIndex);
      msgsRef.current = newMsgs;
      setMsgs(newMsgs);

      await runChatRequest(
        userRow.text,
        lessonId,
        userRow.attachments ?? [],
      );
    },
    [busy, runChatRequest],
  );

  return {
    msgs,
    streaming,
    busy,
    citations,
    lastMeta,
    errorMsg,
    sendMessage,
    commitUserEditReplace,
    regenerateAssistantAt,
    pendingAttachments,
    attachmentUploading,
    addAttachment,
    removeAttachment,
  };
}
