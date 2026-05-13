"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Send,
  Bot,
  AlertCircle,
  Paperclip,
  X,
  FileText,
  Loader2,
  Copy,
  Check,
  Pencil,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  useChatStream,
  type AttachmentRef,
  type ChatMessage,
  type UseChatStreamConfig,
} from "@/lib/hooks/useChatStream";
import { toolEmoji } from "@/lib/chatToolEmoji";
import { EDU_CHAT_ADD_ATTACHMENT_EVENT } from "@/lib/captureElementToPngFile";

type UserMe = {
  qa_collection_enabled?: boolean;
  qa_collection_notice_accepted_at?: string | null;
  agent_identity_bound?: boolean;
};

export type ChatComponentProps =
  | {
      variant?: "course";
      courseId: string;
      hydrateSessionId?: string | null;
      emptyHint?: string;
    }
  | {
      variant: "qa_center";
      sessionId: string | null;
      onSessionResolved?: (sessionId: string) => void;
      emptyHint?: string;
    };

function buildStreamConfig(props: ChatComponentProps): UseChatStreamConfig {
  if (props.variant === "qa_center") {
    return {
      kind: "qa_center_global",
      sessionId: props.sessionId,
      onResolvedSessionId: props.onSessionResolved,
    };
  }
  return {
    kind: "course",
    courseId: props.courseId,
    hydrateSessionId: props.hydrateSessionId ?? null,
  };
}

function defaultEmptyHint(props: ChatComponentProps): string {
  if (props.variant === "qa_center") {
    return "向助手提问，将检索你有权限的全部课程资料";
  }
  return "向我提问关于本节课的任何问题";
}

async function copyToClipboard(text: string): Promise<void> {
  await navigator.clipboard.writeText(text);
}

export default function ChatComponent(props: ChatComponentProps) {
  const [input, setInput] = useState("");
  const [agentIdentityBound, setAgentIdentityBound] = useState<boolean | null>(null);
  const [editingClientId, setEditingClientId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [copiedClientId, setCopiedClientId] = useState<string | null>(null);
  const copyFeedbackTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const {
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
  } = useChatStream(buildStreamConfig(props));
  const scrollRef = useRef<HTMLDivElement>(null);
  const emptyHint = props.emptyHint ?? defaultEmptyHint(props);

  const threadKey =
    props.variant === "qa_center"
      ? props.sessionId ?? ""
      : `${props.courseId}:${props.hydrateSessionId ?? ""}`;

  useEffect(() => {
    setEditingClientId(null);
    setEditDraft("");
  }, [threadKey]);

  const loadUser = useCallback(async () => {
    const res = await fetch("/api/v1/user", { credentials: "include" });
    if (!res.ok) return;
    const u = (await res.json()) as UserMe;
    if (typeof u.agent_identity_bound === "boolean") {
      setAgentIdentityBound(u.agent_identity_bound);
    }
    if (!u.qa_collection_notice_accepted_at) {
      if (window.confirm("为改进教学，我们会记录提问数据。此行为可随时在个人资料关闭。确认知悉？")) {
        await fetch("/api/v1/user", {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ qa_collection_notice_accepted: true }),
        });
      } else {
        await fetch("/api/v1/user", {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ qa_collection_enabled: false }),
        });
      }
    }
  }, []);

  useEffect(() => {
    void loadUser();
  }, [loadUser]);

  useEffect(() => {
    if (props.variant === "qa_center") return;
    const onAddAttachment = (ev: Event) => {
      const ce = ev as CustomEvent<{ file?: File }>;
      const f = ce.detail?.file;
      if (f instanceof File) void addAttachment(f);
    };
    window.addEventListener(EDU_CHAT_ADD_ATTACHMENT_EVENT, onAddAttachment);
    return () =>
      window.removeEventListener(EDU_CHAT_ADD_ATTACHMENT_EVENT, onAddAttachment);
  }, [addAttachment, props.variant]);

  useEffect(() => {
    if (scrollRef.current) {
      const scrollElement = scrollRef.current.querySelector(
        "[data-radix-scroll-area-viewport]",
      );
      if (scrollElement) {
        scrollElement.scrollTop = scrollElement.scrollHeight;
      }
    }
  }, [msgs, streaming, toolActivity]);

  const handleSend = () => {
    if ((!input.trim() && pendingAttachments.length === 0) || busy) return;
    void sendMessage(input);
    setInput("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    files.forEach((f) => void addAttachment(f));
    e.target.value = "";
  };

  const startEdit = (msg: ChatMessage) => {
    setEditingClientId(msg.clientId);
    setEditDraft(msg.text);
  };

  const cancelEdit = () => {
    setEditingClientId(null);
    setEditDraft("");
  };

  const submitEdit = (msgIndex: number) => {
    if (busy) return;
    void commitUserEditReplace(msgIndex, editDraft).then((ok) => {
      if (ok) cancelEdit();
    });
  };

  return (
    <div className="flex flex-col h-full bg-background relative">
      {agentIdentityBound === false && (
        <div className="shrink-0 border-b border-amber-500/25 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-950 dark:text-amber-100">
          <div className="w-full max-w-none flex flex-wrap items-center gap-2">
            <AlertCircle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
            <span>
              当前账号尚未绑定 Edu Agent。请先在 Agent 侧执行绑定（edu bind），再到本平台的
            </span>
            <Link
              href="/credentials"
              className="font-medium text-amber-900 underline underline-offset-2 hover:text-amber-800 dark:text-amber-200 dark:hover:text-amber-50"
            >
              凭证
            </Link>
            <span>页完成关联后再使用 AI 聊天。</span>
          </div>
        </div>
      )}
      <ScrollArea ref={scrollRef} className="flex-1 w-full px-4 md:px-8 pt-6 pb-32">
        <div className="w-full max-w-none space-y-8 flex flex-col">
          {msgs.length === 0 && !streaming && !busy && (
            <div className="h-[50vh] flex flex-col items-center justify-center text-muted-foreground opacity-50">
              <Bot className="h-16 w-16 mb-4" />
              <p>{emptyHint}</p>
            </div>
          )}

          {msgs.map((msg, i) => {
            const isEditing = editingClientId === msg.clientId;
            const branchRecord = branchRecords.find((r) => r.position === i);
            const hasBranch = !!branchRecord && branchRecord.entries.length > 1;
            return (
              <div key={msg.clientId} className="group flex w-full flex-col">
                <div
                  className={cn(
                    "flex w-full",
                    msg.role === "user" ? "justify-end" : "justify-start",
                  )}
                >
                  <div
                    className={cn(
                      "flex min-w-0 flex-col",
                      msg.role === "user"
                        ? "max-w-[min(100%,80%)] items-end"
                        : "w-full min-w-0 items-start",
                    )}
                  >
                  {msg.attachments && msg.attachments.length > 0 && (
                    <div
                      className={cn(
                        "flex flex-wrap gap-1.5 mb-1.5",
                        msg.role === "user" ? "justify-end" : "justify-start",
                      )}
                    >
                      {msg.attachments.map((att) => (
                        <AttachmentBubble key={att.id} att={att} />
                      ))}
                    </div>
                  )}

                  <div
                    className={cn(
                      "relative",
                      msg.role === "user"
                        ? "rounded-2xl bg-muted text-foreground rounded-tr-sm"
                        : "rounded-none bg-transparent text-foreground",
                    )}
                  >
                    {msg.role === "user" && isEditing ? (
                      <div className="flex flex-col gap-2 p-3 min-w-[min(100%,280px)]">
                        <Textarea
                          value={editDraft}
                          onChange={(e) => setEditDraft(e.target.value)}
                          className="min-h-[100px] resize-y text-sm"
                          disabled={busy}
                          autoFocus
                        />
                        <div className="flex justify-end gap-2">
                          <Button type="button" variant="ghost" size="sm" onClick={cancelEdit} disabled={busy}>
                            取消
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            onClick={() => submitEdit(i)}
                            disabled={
                              busy ||
                              (!editDraft.trim() && (msg.attachments?.length ?? 0) === 0)
                            }
                          >
                            发送
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <div
                        className={cn(
                          "px-4 py-3",
                          msg.role === "assistant" &&
                            "prose prose-sm dark:prose-invert max-w-none [&_pre]:border-0",
                        )}
                      >
                        {msg.role === "user" ? (
                          <div className="whitespace-pre-wrap">{msg.text}</div>
                        ) : (
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Branch navigation arrows — shown below each message bubble */}
                  {hasBranch && !isEditing && (
                    <div
                      className={cn(
                        "flex items-center gap-0.5 mt-0.5 text-xs text-muted-foreground",
                        msg.role === "user" ? "justify-end" : "justify-start",
                      )}
                    >
                      <button
                        type="button"
                        disabled={branchRecord.activeIdx === 0 || busy}
                        onClick={() => navigateBranch(i, -1)}
                        className="h-5 w-5 inline-flex items-center justify-center rounded hover:bg-muted disabled:opacity-30 transition-colors"
                        aria-label="上一个版本"
                      >
                        <ChevronLeft size={13} />
                      </button>
                      <span className="tabular-nums px-0.5 select-none">
                        {branchRecord.activeIdx + 1}&thinsp;/&thinsp;{branchRecord.entries.length}
                      </span>
                      <button
                        type="button"
                        disabled={branchRecord.activeIdx === branchRecord.entries.length - 1 || busy}
                        onClick={() => navigateBranch(i, 1)}
                        className="h-5 w-5 inline-flex items-center justify-center rounded hover:bg-muted disabled:opacity-30 transition-colors"
                        aria-label="下一个版本"
                      >
                        <ChevronRight size={13} />
                      </button>
                    </div>
                  )}

                  <div
                    className={cn(
                      "mt-1 flex items-center gap-0.5 opacity-100 sm:opacity-0 sm:transition-opacity sm:group-hover:opacity-100",
                      msg.role === "user" ? "flex-row-reverse" : "flex-row",
                    )}
                  >
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-muted-foreground hover:text-foreground"
                      disabled={busy}
                      aria-label="复制消息"
                      onClick={() => void copyToClipboard(msg.text).catch(() => {})}
                    >
                      <Copy size={14} strokeWidth={2} />
                    </Button>
                    {msg.role === "user" && !isEditing && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-muted-foreground hover:text-foreground"
                        disabled={busy}
                        aria-label="编辑消息"
                        onClick={() => startEdit(msg)}
                      >
                        <Pencil size={14} strokeWidth={2} />
                      </Button>
                    )}
                    {msg.role === "assistant" && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-muted-foreground hover:text-foreground"
                        disabled={busy}
                        aria-label="重新生成"
                        onClick={() => void regenerateAssistantAt(i)}
                      >
                        <RefreshCw size={14} strokeWidth={2} />
                      </Button>
                    )}
                  </div>
                  {/* Historical tool activity & citations (loaded from DB) */}
                  {msg.role === "assistant" && (msg.toolActivity?.length ?? 0) + (msg.citations?.length ?? 0) > 0 && (
                    <div className="flex flex-col gap-1.5 mt-1.5">
                      {(msg.toolActivity?.length ?? 0) > 0 && (
                        <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm text-muted-foreground">
                          {msg.toolActivity!.map((row, ri) => (
                            <li
                              key={row.clientKey ?? `${msg.clientId}-tc-${ri}`}
                              className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 bg-muted/40 px-2.5 py-1.5"
                            >
                              <span className="tabular-nums" aria-hidden>{toolEmoji(row.name)}</span>
                              <span className="font-mono text-xs text-foreground/90">{row.name}</span>
                              <span className="text-xs">
                                <span className={row.success === false ? "text-destructive" : "text-emerald-600 dark:text-emerald-400"}>
                                  {row.success === false ? "✗" : "✓"}
                                </span>
                                {typeof row.durationMs === "number" && (
                                  <span className="ml-1.5 opacity-80">{(row.durationMs / 1000).toFixed(1)}s</span>
                                )}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                      {(msg.citations?.length ?? 0) > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {msg.citations!.map((c, ci) => (
                            <button
                              key={ci}
                              type="button"
                              onClick={() => {
                                window.dispatchEvent(
                                  new CustomEvent("edu:open-material-preview", {
                                    detail: {
                                      materialId: c.material_id,
                                      chunkId: c.chunk_id,
                                      sourceLabel: c.source_label ?? `引用 ${ci + 1}`,
                                      chunkText: c.chunk_text,
                                    },
                                  }),
                                );
                              }}
                              className="inline-flex items-center gap-1 rounded-full border border-primary/25 bg-primary/8 px-2.5 py-0.5 text-[11px] font-medium text-primary hover:bg-primary/15 transition-colors"
                            >
                              <span className="font-mono font-bold opacity-60">[{ci + 1}]</span>
                              <span className="max-w-[140px] truncate">{c.source_label ?? `引用 ${ci + 1}`}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
              </div>
            );
          })}

          {busy && (!streaming || toolActivity.length > 0) && (
            <div className="flex w-full flex-col gap-2 pl-4">
              {!streaming && (
                <div
                  className="flex items-center gap-2 text-sm text-muted-foreground"
                  aria-live="polite"
                  aria-busy="true"
                >
                  <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden />
                  <span>思考中…</span>
                </div>
              )}
              {toolActivity.length > 0 && (
                <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm text-muted-foreground">
                  {toolActivity.map((row) => (
                    <li
                      key={row.clientKey}
                      className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 bg-muted/40 px-2.5 py-1.5"
                    >
                      <span className="tabular-nums" aria-hidden>
                        {toolEmoji(row.name)}
                      </span>
                      <span className="font-mono text-xs text-foreground/90">{row.name}</span>
                      {row.status === "running" && (
                        <Loader2
                          className="h-3.5 w-3.5 shrink-0 animate-spin opacity-70"
                          aria-label="执行中"
                        />
                      )}
                      {row.status === "done" && (
                        <span className="text-xs">
                          <span
                            className={
                              row.success === false
                                ? "text-destructive"
                                : "text-emerald-600 dark:text-emerald-400"
                            }
                          >
                            {row.success === false ? "✗" : "✓"}
                          </span>
                          {typeof row.durationMs === "number" && (
                            <span className="ml-1.5 opacity-80">
                              {(row.durationMs / 1000).toFixed(1)}s
                            </span>
                          )}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {streaming && (
            <div className="group flex w-full flex-col">
              <div className="flex w-full justify-start">
                <div className="flex min-w-0 w-full flex-col items-start">
                  <div className="rounded-none px-4 py-3 bg-transparent text-foreground prose prose-sm dark:prose-invert max-w-none [&_pre]:border-0">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{streaming}</ReactMarkdown>
                    <span className="inline-block w-2 h-4 bg-primary animate-pulse ml-1 align-middle" />
                  </div>
                  <div className="mt-1 flex opacity-100 sm:opacity-0 sm:transition-opacity sm:group-hover:opacity-100">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-muted-foreground hover:text-foreground"
                      disabled={streaming.length === 0}
                      aria-label="复制正在生成的内容"
                      onClick={() => void copyToClipboard(streaming).catch(() => {})}
                    >
                      <Copy size={14} strokeWidth={2} />
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {(citations.length > 0 || lastMeta) && (
            <div className="flex w-full justify-start pl-0">
              <div className="space-y-1.5">
                {citations.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {citations.map((c, ci) => (
                      <button
                        key={ci}
                        type="button"
                        onClick={() => {
                          window.dispatchEvent(
                            new CustomEvent("edu:open-material-preview", {
                              detail: {
                                materialId: c.material_id,
                                chunkId: c.chunk_id,
                                sourceLabel: c.source_label ?? `引用 ${ci + 1}`,
                                chunkText: c.chunk_text,
                              },
                            }),
                          );
                        }}
                        className="inline-flex items-center gap-1 rounded-full border border-primary/25 bg-primary/8 px-2.5 py-0.5 text-[11px] font-medium text-primary hover:bg-primary/15 transition-colors"
                      >
                        <span className="font-mono font-bold opacity-60">[{ci + 1}]</span>
                        <span className="max-w-[140px] truncate">
                          {c.source_label ?? `引用 ${ci + 1}`}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
                {lastMeta?.type === "done" && !lastMeta.error && lastMeta.exec_time_ms && (
                  <p className="text-[10px] text-muted-foreground/60">{lastMeta.exec_time_ms} ms</p>
                )}
              </div>
            </div>
          )}

          {errorMsg && (
            <div className="flex w-full justify-center">
              <div className="flex items-center gap-2 text-xs text-destructive bg-destructive/10 px-3 py-1.5 rounded-full">
                <AlertCircle size={14} />
                <span>{errorMsg}</span>
              </div>
            </div>
          )}
        </div>
      </ScrollArea>

      <div className="absolute bottom-4 left-0 right-0 w-full px-4 md:px-8 bg-gradient-to-t from-background via-background to-transparent pt-6">
        <div className="w-full max-w-none relative rounded-2xl bg-muted/40 border border-border shadow-sm focus-within:ring-1 focus-within:ring-ring focus-within:bg-background transition-colors overflow-hidden">
          {pendingAttachments.length > 0 && (
            <div className="flex flex-wrap gap-2 px-4 pt-3 pb-1">
              {pendingAttachments.map((att) => (
                <div key={att.id} className="relative group flex-shrink-0">
                  {att.mime_type.startsWith("image/") && att.localPreviewUrl ? (
                    <img
                      src={att.localPreviewUrl}
                      alt={att.name}
                      className="w-16 h-16 object-cover rounded-lg border border-border"
                    />
                  ) : (
                    <div className="w-16 h-16 flex flex-col items-center justify-center rounded-lg border border-border bg-muted text-xs text-muted-foreground gap-1 px-1">
                      <FileText size={20} className="shrink-0" />
                      <span className="truncate w-full text-center leading-tight">{att.name}</span>
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={() => removeAttachment(att.id)}
                    className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-foreground text-background flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                    aria-label="移除附件"
                  >
                    <X size={10} />
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="flex items-end">
            <div className="pl-2 pb-2 flex-shrink-0">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept="image/*,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.txt,.md"
                className="hidden"
                onChange={handleFileChange}
              />
              <Button
                size="icon"
                variant="ghost"
                className={cn(
                  "h-8 w-8 rounded-full text-muted-foreground hover:text-foreground",
                  attachmentUploading && "opacity-50",
                )}
                onClick={() => fileInputRef.current?.click()}
                disabled={attachmentUploading || busy}
                aria-label="上传附件"
              >
                {attachmentUploading ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <Paperclip size={16} />
                )}
              </Button>
            </div>
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="消息 Eduardo..."
              className="min-h-[52px] max-h-48 resize-none border-0 shadow-none bg-transparent py-4 text-sm focus-visible:ring-0 flex-1"
              disabled={busy}
              rows={1}
            />
            <div className="pr-2 pb-2 flex-shrink-0">
              <Button
                size="icon"
                className={cn(
                  "h-8 w-8 rounded-full",
                  ((!input.trim() && pendingAttachments.length === 0) || busy) && "opacity-50",
                )}
                onClick={handleSend}
                disabled={(!input.trim() && pendingAttachments.length === 0) || busy}
              >
                <Send size={16} />
              </Button>
            </div>
          </div>
        </div>
        <div className="text-center mt-2 text-xs text-muted-foreground">
          EduAgent 可能会犯错。请补充核实。
        </div>
      </div>
    </div>
  );
}

function AttachmentBubble({ att }: { att: AttachmentRef }) {
  const imgSrc =
    att.mime_type.startsWith("image/") && (att.localPreviewUrl ?? att.presigned_url)
      ? (att.localPreviewUrl ?? att.presigned_url)
      : null;

  if (imgSrc) {
    return (
      <img
        src={imgSrc}
        alt={att.name}
        className="max-w-[200px] max-h-[200px] rounded-xl object-cover border border-border"
      />
    );
  }
  return (
    <div className="flex items-center gap-2 rounded-xl border border-border bg-muted px-3 py-2 text-sm text-muted-foreground max-w-[200px]">
      <FileText size={16} className="shrink-0" />
      <span className="truncate">{att.name}</span>
    </div>
  );
}
