"use client";

import { useCallback, useEffect, useState } from "react";
import {
  MessageSquare,
  PanelLeftClose,
  PanelRight,
  Pencil,
  Trash2,
  Loader2,
  X,
  FileText,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import ChatComponent from "@/components/ChatComponent";

type CitationPanel = {
  materialId: string;
  chunkId?: string;
  sourceLabel?: string;
  chunkText?: string;
};

type ThreadRow = {
  session_id: string;
  kind: "course" | "global";
  course_id: string | null;
  course_name: string | null;
  title: string;
  last_message_at: string;
  has_messages: boolean;
};

export default function QaCenterPage() {
  const [threads, setThreads] = useState<ThreadRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<ThreadRow | null>(null);
  const [listCollapsed, setListCollapsed] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [citationPanel, setCitationPanel] = useState<CitationPanel | null>(null);

  useEffect(() => {
    const h = (ev: Event) => {
      const ce = ev as CustomEvent<CitationPanel>;
      if (ce.detail?.materialId) {
        setCitationPanel(ce.detail);
      }
    };
    window.addEventListener("edu:open-material-preview", h as EventListener);
    return () => window.removeEventListener("edu:open-material-preview", h as EventListener);
  }, []);

  const loadThreads = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/me/chat-threads", { credentials: "include" });
      if (!res.ok) return;
      const body = (await res.json()) as { threads?: ThreadRow[] };
      setThreads(Array.isArray(body.threads) ? body.threads : []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadThreads();
  }, [loadThreads]);

  const handleNewGlobal = async () => {
    const res = await fetch("/api/v1/me/chat-threads", {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) {
      const j = (await res.json().catch(() => ({}))) as { error?: { message?: string } };
      alert(j.error?.message ?? "创建失败");
      return;
    }
    const body = (await res.json()) as { session_id?: string };
    if (!body.session_id) return;
    const row: ThreadRow = {
      session_id: body.session_id,
      kind: "global",
      course_id: null,
      course_name: null,
      title: "新对话",
      last_message_at: new Date().toISOString(),
      has_messages: false,
    };
    setThreads((prev) => [row, ...prev.filter((t) => t.session_id !== row.session_id)]);
    setSelected(row);
    setListCollapsed(false);
  };

  const handleDelete = async () => {
    if (!selected) return;
    if (!window.confirm("删除此对话及其消息？")) return;
    const res = await fetch(
      `/api/v1/me/chat-threads/${encodeURIComponent(selected.session_id)}`,
      { method: "DELETE", credentials: "include" },
    );
    if (!res.ok) {
      alert("删除失败");
      return;
    }
    setSelected(null);
    void loadThreads();
  };

  const startEditTitle = () => {
    if (!selected) return;
    setTitleDraft(selected.title);
    setEditingTitle(true);
  };

  const saveTitle = async () => {
    if (!selected) return;
    const t = titleDraft.trim();
    if (!t) {
      setEditingTitle(false);
      return;
    }
    const res = await fetch(
      `/api/v1/me/chat-threads/${encodeURIComponent(selected.session_id)}`,
      {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: t }),
      },
    );
    if (!res.ok) {
      alert("保存标题失败");
      return;
    }
    setSelected((s) => (s ? { ...s, title: t } : s));
    setThreads((prev) =>
      prev.map((x) => (x.session_id === selected.session_id ? { ...x, title: t } : x)),
    );
    setEditingTitle(false);
  };

  const onSessionResolved = useCallback(() => {
    void loadThreads();
  }, [loadThreads]);

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden bg-background">
      <div className="flex items-center justify-between border-b border-border px-4 py-3 shrink-0">
        <h1 className="font-display text-sm font-semibold">问答中心</h1>
        <p className="text-xs text-muted-foreground hidden sm:block">
          跨课程检索 · 历史仅展示已落库的问答
        </p>
      </div>

      <div className="flex flex-1 min-h-0 relative">
        <aside
          className={cn(
            "flex flex-col border-r border-border bg-muted/20 transition-[width,opacity] duration-200 ease-out shrink-0",
            listCollapsed ? "w-0 min-w-0 opacity-0 overflow-hidden border-r-0" : "w-[min(280px,36vw)] min-w-[220px] opacity-100",
          )}
        >
          <div className="flex items-center gap-2 p-2 border-b border-border shrink-0">
            <Button size="sm" className="flex-1" onClick={() => void handleNewGlobal()}>
              新建对话
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="shrink-0"
              aria-label="收起列表"
              onClick={() => setListCollapsed(true)}
            >
              <PanelLeftClose size={18} />
            </Button>
          </div>
          <ScrollArea className="flex-1">
            <div className="p-2 space-y-1">
              {loading && (
                <div className="flex justify-center py-8 text-muted-foreground">
                  <Loader2 className="animate-spin" size={20} />
                </div>
              )}
              {!loading &&
                threads.map((t) => (
                  <button
                    key={t.session_id}
                    type="button"
                    onClick={() => {
                      setSelected(t);
                      setEditingTitle(false);
                    }}
                    className={cn(
                      "w-full text-left rounded-lg px-3 py-2.5 text-sm transition-colors",
                      selected?.session_id === t.session_id
                        ? "bg-accent text-accent-foreground font-medium"
                        : "hover:bg-muted/80 text-foreground",
                    )}
                  >
                    <div className="flex items-start gap-2">
                      <MessageSquare
                        size={16}
                        className="shrink-0 mt-0.5 opacity-60"
                      />
                      <span className="line-clamp-2 leading-snug">{t.title}</span>
                    </div>
                    {t.kind === "course" && t.course_name && (
                      <p className="text-[10px] text-muted-foreground mt-1 pl-7 truncate">
                        {t.course_name}
                      </p>
                    )}
                  </button>
                ))}
            </div>
          </ScrollArea>
        </aside>

        {listCollapsed && (
          <Button
            type="button"
            size="icon"
            variant="outline"
            className="absolute left-2 top-2 z-20 h-9 w-9 shadow-sm"
            aria-label="展开对话列表"
            onClick={() => setListCollapsed(false)}
          >
            <PanelRight size={18} />
          </Button>
        )}

        <div className="flex-1 flex min-w-0 min-h-0">
          <div className="flex-1 flex flex-col min-w-0 min-h-0">
          {selected && (
            <div className="flex items-center gap-2 border-b border-border px-3 py-2 shrink-0 bg-background/95">
              {editingTitle ? (
                <>
                  <Input
                    value={titleDraft}
                    onChange={(e) => setTitleDraft(e.target.value)}
                    className="h-8 text-sm max-w-md"
                    maxLength={200}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void saveTitle();
                      if (e.key === "Escape") setEditingTitle(false);
                    }}
                  />
                  <Button size="sm" variant="secondary" onClick={() => void saveTitle()}>
                    保存
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditingTitle(false)}>
                    取消
                  </Button>
                </>
              ) : (
                <>
                  <span className="text-sm font-medium truncate flex-1">{selected.title}</span>
                  <Button size="icon" variant="ghost" className="h-8 w-8" onClick={startEditTitle}>
                    <Pencil size={16} />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-8 w-8 text-destructive hover:text-destructive"
                    onClick={() => void handleDelete()}
                  >
                    <Trash2 size={16} />
                  </Button>
                </>
              )}
            </div>
          )}

          <div className="flex-1 min-h-0 flex flex-col">
            {!selected && (
              <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground text-sm px-6 text-center gap-3">
                <MessageSquare className="h-12 w-12 opacity-30" />
                <p>选择左侧对话，或新建跨课程问答</p>
                <Button variant="outline" size="sm" onClick={() => void handleNewGlobal()}>
                  新建对话
                </Button>
              </div>
            )}
            {selected?.kind === "global" && (
              <ChatComponent
                key={selected.session_id}
                variant="qa_center"
                sessionId={selected.session_id}
                onSessionResolved={onSessionResolved}
              />
            )}
            {selected?.kind === "course" && selected.course_id && (
              <ChatComponent
                key={selected.session_id}
                courseId={selected.course_id}
                hydrateSessionId={selected.session_id}
              />
            )}
          </div>
          </div>

          {/* Citation preview sidebar */}
          <aside
            className={cn(
              "flex flex-col border-l border-border bg-background transition-[width,opacity] duration-200 ease-out shrink-0 overflow-hidden",
              citationPanel
                ? "w-[min(420px,44vw)] min-w-[280px] opacity-100"
                : "w-0 min-w-0 opacity-0",
            )}
          >
            {citationPanel && (
              <>
                <div className="flex items-center gap-2 px-3 py-2 border-b border-border shrink-0 bg-muted/30">
                  <span className="text-xs font-medium text-muted-foreground truncate flex-1">
                    {citationPanel.sourceLabel ?? "引用资料"}
                  </span>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7 shrink-0"
                    aria-label="关闭引用面板"
                    onClick={() => setCitationPanel(null)}
                  >
                    <X size={15} />
                  </Button>
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto">
                  {citationPanel.chunkText ? (
                    <div className="p-4 space-y-3">
                      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <FileText size={13} />
                        <span>检索文本块</span>
                      </div>
                      <pre className="whitespace-pre-wrap text-xs leading-relaxed font-mono bg-muted/40 rounded-lg p-3 border border-border text-foreground">
                        {citationPanel.chunkText}
                      </pre>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center h-full min-h-[120px] text-muted-foreground text-xs gap-2 p-6 text-center">
                      <FileText size={24} className="opacity-40" />
                      <span>暂无文本块内容</span>
                    </div>
                  )}
                </div>
              </>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}
