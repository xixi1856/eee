"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type ChatMessage = {
  clientId: string;
  role: "user" | "assistant";
  text: string;
  attachments?: AttachmentRef[];
  /** Same QaLog row id for hydrated user/assistant pair */
  qaLogId?: string;
  /** Tool calls recorded during this turn (populated from history) */
  toolActivity?: ToolActivityItem[];
  /** Citations recorded during this turn (populated from history) */
  citations?: Citation[];
};

/**
 * A single branch snapshot stored when the user edits a message or regenerates a reply.
 * `tailMsgs` contains the msgs array starting from the branch position (inclusive).
 */
export type BranchEntry = {
  tailMsgs: ChatMessage[];
};

/**
 * Branch history for a specific position in the msgs array.
 * `position` is the 0-based index of the message (user msg for edits, assistant msg for regen).
 */
export type BranchRecord = {
  position: number;
  entries: BranchEntry[];
  activeIdx: number;
};

export type Citation = {
  chunk_id?: string;
  material_id?: string;
  source_label?: string;
  chunk_text?: string;
  image_urls?: Array<{ page_idx: number; url: string }>;
};
export type DoneMeta = {
  type: "done";
  tokens?: number;
  exec_time_ms?: number;
  error?: string;
};

/** In-flight tool row for the current assistant turn (not persisted). */
export type ToolActivityItem = {
  clientKey: string;
  name: string;
  status: "running" | "done";
  success?: boolean;
  durationMs?: number;
};

/** Pending tool-approval request emitted by the ReAct loop. */
export type PendingApprovalState = {
  toolCallId: string;
  toolName: string;
  argsPreview: Record<string, unknown>;
  approvalKey: string;
  reason: string;
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
  error?: { message?: string };
};

function formatChatHttpError(status: number, body: ApiErrJson): string {
  const raw = (body.error?.message ?? "").trim();
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
  tool_calls?: unknown[];
  citations?: unknown[];
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
        toolActivity: Array.isArray(r.tool_calls)
          ? (r.tool_calls as ToolActivityItem[])
          : [],
        citations: Array.isArray(r.citations)
          ? (r.citations as Citation[])
          : [],
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
  const [toolActivity, setToolActivity] = useState<ToolActivityItem[]>([]);
  const toolSeqRef = useRef(0);
  const [busy, setBusy] = useState(false);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [lastMeta, setLastMeta] = useState<DoneMeta | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [pendingAttachments, setPendingAttachments] = useState<AttachmentRef[]>([]);
  const [attachmentUploading, setAttachmentUploading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const [branchRecords, setBranchRecords] = useState<BranchRecord[]>([]);
  const branchRecordsRef = useRef<BranchRecord[]>([]);
  branchRecordsRef.current = branchRecords;

  const [pendingApproval, setPendingApproval] = useState<PendingApprovalState | null>(null);
  const pendingApprovalRef = useRef<PendingApprovalState | null>(null);
  pendingApprovalRef.current = pendingApproval;

  /** Update branch records atomically (keeps ref in sync). */
  const updateBranchRecords = useCallback(
    (updater: (prev: BranchRecord[]) => BranchRecord[]) => {
      const next = updater(branchRecordsRef.current);
      branchRecordsRef.current = next;
      setBranchRecords(next);
    },
    [],
  );

  /**
   * Respond to a pending tool approval request.
   * Sends the user's decision to the server, which signals the paused ReAct loop.
   */
  const respondToApproval = useCallback(async (approved: boolean) => {
    const current = pendingApprovalRef.current;
    if (!current) return;
    try {
      await fetch("/api/v1/chat/approval", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ approval_key: current.approvalKey, approved }),
      });
    } catch {
      // The approval loop will time out on its own; nothing critical to handle here
    }
  }, []);

  const historyLoadKey =
    config.kind === "qa_center_global"
      ? `q:${config.sessionId ?? ""}`
      : `c:${config.courseId}:h:${config.hydrateSessionId ?? ""}`;

  useEffect(() => {
    // Clear branch history whenever we switch threads
    branchRecordsRef.current = [];
    setBranchRecords([]);

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
    async (message: string, lessonId: string | undefined, attachmentRefs: AttachmentRef[], trimHistoryTo?: number) => {
      const config = cfgRef.current;
      setBusy(true);
      setStreaming("");
      setToolActivity([]);
      toolSeqRef.current = 0;
      setCitations([]);
      setLastMeta(null);
      setErrorMsg(null);
      setPendingApproval(null);
      pendingApprovalRef.current = null;

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
          ...(trimHistoryTo !== undefined ? { trim_history_to: trimHistoryTo } : {}),
        };
        if (isQaGlobal && config.sessionId) {
          body.session_id = config.sessionId;
        }
        if (config.kind === "course" && config.hydrateSessionId) {
          body.session_id = config.hydrateSessionId;
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
                chunk_text?: string;
                image_urls?: Array<{ page_idx: number; url: string }>;
                tokens?: number;
                exec_time_ms?: number;
                error?: string;
                name?: string;
                tool_call_id?: string;
                success?: boolean;
                duration_ms?: number;
                // approval fields
                tool_name?: string;
                args_preview?: Record<string, unknown>;
                approval_key?: string;
                reason?: string;
                approved?: boolean;
              };

              if (event.type === "text" && event.content) {
                streamText += event.content;
                setStreaming(streamText);
              } else if (event.type === "tool_call" && event.name) {
                const id =
                  typeof event.tool_call_id === "string" && event.tool_call_id
                    ? event.tool_call_id
                    : `tc-${++toolSeqRef.current}`;
                setToolActivity((prev) => [
                  ...prev,
                  { clientKey: id, name: event.name!, status: "running" },
                ]);
              } else if (event.type === "tool_result" && event.name) {
                const toolName = event.name;
                setToolActivity((prev) => {
                  let runIdx = -1;
                  for (let i = prev.length - 1; i >= 0; i--) {
                    if (prev[i].status === "running" && prev[i].name === toolName) {
                      runIdx = i;
                      break;
                    }
                  }
                  if (runIdx === -1) return prev;
                  const next = [...prev];
                  next[runIdx] = {
                    ...next[runIdx],
                    status: "done",
                    success: event.success,
                    durationMs: event.duration_ms,
                  };
                  return next;
                });
              } else if (event.type === "citation") {
                newCitations.push({
                  chunk_id: event.chunk_id,
                  material_id: event.material_id,
                  source_label: event.source_label,
                  chunk_text: event.chunk_text,
                  image_urls: event.image_urls,
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
              } else if (event.type === "require_approval") {
                const state: PendingApprovalState = {
                  toolCallId: event.tool_call_id ?? "",
                  toolName: event.tool_name ?? "",
                  argsPreview: event.args_preview ?? {},
                  approvalKey: event.approval_key ?? "",
                  reason: event.reason ?? "此操作需要您的确认。",
                };
                pendingApprovalRef.current = state;
                setPendingApproval(state);
              } else if (event.type === "approval_resolved") {
                pendingApprovalRef.current = null;
                setPendingApproval(null);
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
        setToolActivity([]);
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
        setToolActivity([]);
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

      const attachments = row.attachments ?? [];
      const newUser: ChatMessage = {
        clientId: newClientId(),
        role: "user",
        text: trimmed,
        ...(attachments.length ? { attachments } : {}),
      };

      // ── Branch tracking: save the current tail before overwriting ──
      const turnPosition = replaceFromIndex;
      const oldTailMsgs = prev.slice(turnPosition);
      updateBranchRecords((prevRecords) => {
        const existingIdx = prevRecords.findIndex((r) => r.position === turnPosition);
        if (existingIdx !== -1) {
          // Branch already exists; current state is already stored as one of the entries.
          return prevRecords;
        }
        // Remove stale records at positions >= turnPosition, then add new record.
        const cleaned = prevRecords.filter((r) => r.position < turnPosition);
        return [
          ...cleaned,
          { position: turnPosition, entries: [{ tailMsgs: oldTailMsgs }], activeIdx: 0 },
        ];
      });

      const newMsgs = [...prev.slice(0, replaceFromIndex), newUser];
      msgsRef.current = newMsgs;
      setMsgs(newMsgs);

      await runChatRequest(trimmed, lessonId, attachments, replaceFromIndex);

      // After API response, capture new tail and add as the next branch entry.
      const newTailMsgs = msgsRef.current.slice(turnPosition);
      updateBranchRecords((prevRecords) => {
        const idx = prevRecords.findIndex((r) => r.position === turnPosition);
        if (idx === -1) return prevRecords;
        const record = prevRecords[idx];
        const newEntries = [...record.entries, { tailMsgs: newTailMsgs }];
        const updated: BranchRecord = { ...record, entries: newEntries, activeIdx: newEntries.length - 1 };
        const next = [...prevRecords];
        next[idx] = updated;
        return next;
      });

      return true;
    },
    [busy, runChatRequest, updateBranchRecords],
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

      // ── Branch tracking: save the current assistant tail before overwriting ──
      // We track at the assistant message position so arrows appear on the assistant bubble.
      const turnPosition = assistantIndex;
      const oldTailMsgs = prev.slice(turnPosition);
      updateBranchRecords((prevRecords) => {
        const existingIdx = prevRecords.findIndex((r) => r.position === turnPosition);
        if (existingIdx !== -1) {
          return prevRecords;
        }
        const cleaned = prevRecords.filter((r) => r.position < turnPosition);
        return [
          ...cleaned,
          { position: turnPosition, entries: [{ tailMsgs: oldTailMsgs }], activeIdx: 0 },
        ];
      });

      const newMsgs = prev.slice(0, assistantIndex);
      msgsRef.current = newMsgs;
      setMsgs(newMsgs);

      await runChatRequest(
        userRow.text,
        lessonId,
        userRow.attachments ?? [],
        assistantIndex - 1,
      );

      // After API response, capture new assistant tail and add as next branch entry.
      const newTailMsgs = msgsRef.current.slice(turnPosition);
      updateBranchRecords((prevRecords) => {
        const idx = prevRecords.findIndex((r) => r.position === turnPosition);
        if (idx === -1) return prevRecords;
        const record = prevRecords[idx];
        const newEntries = [...record.entries, { tailMsgs: newTailMsgs }];
        const updated: BranchRecord = { ...record, entries: newEntries, activeIdx: newEntries.length - 1 };
        const next = [...prevRecords];
        next[idx] = updated;
        return next;
      });
    },
    [busy, runChatRequest, updateBranchRecords],
  );

  /** Navigate between historical branches at the given msg index. */
  const navigateBranch = useCallback(
    (position: number, direction: -1 | 1) => {
      if (busy) return;
      const records = branchRecordsRef.current;
      const recordIdx = records.findIndex((r) => r.position === position);
      if (recordIdx === -1) return;
      const record = records[recordIdx];
      const newIdx = record.activeIdx + direction;
      if (newIdx < 0 || newIdx >= record.entries.length) return;

      const newRecord: BranchRecord = { ...record, activeIdx: newIdx };
      // Clear stale records at positions > this branch point (they belong to a different subtree).
      const cleanedRecords = records
        .filter((r) => r.position <= position)
        .map((r) => (r.position === position ? newRecord : r));
      branchRecordsRef.current = cleanedRecords;
      setBranchRecords(cleanedRecords);

      // Reconstruct msgs from the selected snapshot.
      const targetEntry = record.entries[newIdx];
      const prefix = msgsRef.current.slice(0, position);
      const newMsgs = [...prefix, ...targetEntry.tailMsgs];
      msgsRef.current = newMsgs;
      setMsgs(newMsgs);
    },
    [busy],
  );

  return {
    msgs,
    streaming,
    toolActivity,
    busy,
    citations,
    lastMeta,
    errorMsg,
    sendMessage,
    commitUserEditReplace,
    regenerateAssistantAt,
    navigateBranch,
    branchRecords,
    pendingAttachments,
    attachmentUploading,
    addAttachment,
    removeAttachment,
    pendingApproval,
    respondToApproval,
  };
}
