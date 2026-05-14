"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTheme } from "next-themes";
import type { DockviewIDisposable } from "dockview";
import {
  DockviewReact,
  themeDark,
  themeLight,
  type DockviewApi,
  type DockviewReadyEvent,
  type IDockviewPanelProps,
} from "dockview";
import "dockview/dist/styles/dockview.css";
import { cn } from "@/lib/utils";
import { X, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Image from "next/image";
import ChatComponent from "@/components/ChatComponent";
import CourseMaterialList from "@/components/CourseMaterialList";
import CourseMaterialViewer from "@/components/CourseMaterialViewer";

type CitationPreview = {
  materialId: string;
  chunkId?: string;
  sourceLabel?: string;
  chunkText?: string;
  image_urls?: Array<{ page_idx: number; url: string }>;
};

type CourseChatCtx = {
  courseId: string;
  activeMaterialId: string | null;
  onPickMaterial: (id: string) => void;
  citation: CitationPreview | null;
  /** When set, ChatComponent hydrates transcript from QA thread API. */
  hydrateSessionId: string | null;
};

const CourseChatCtx = createContext<CourseChatCtx | null>(null);

function useCourseChatCtx(): CourseChatCtx {
  const v = useContext(CourseChatCtx);
  if (!v) throw new Error("CourseChatDockview context missing");
  return v;
}

function dockviewLayoutKey(courseId: string): string {
  return `edu:course-chat:dockview:${courseId}`;
}

function defaultThreeColumnLayout(api: DockviewApi): void {
  const list = api.addPanel({
    id: "materialList",
    component: "materialList",
    title: "资料列表",
  });
  const preview = api.addPanel({
    id: "materialPreview",
    component: "materialPreview",
    title: "资料预览",
    position: { referencePanel: list, direction: "right" },
  });
  api.addPanel({
    id: "chat",
    component: "chat",
    title: "课程问答",
    position: { referencePanel: preview, direction: "right" },
  });
}

function isPersistedDockviewLayout(
  x: unknown,
): x is Parameters<DockviewApi["fromJSON"]>[0] {
  if (typeof x !== "object" || x === null) return false;
  const o = x as Record<string, unknown>;
  return (
    typeof o.grid === "object" &&
    o.grid !== null &&
    typeof o.panels === "object" &&
    o.panels !== null
  );
}

function MaterialListPanel(_props: IDockviewPanelProps) {
  const { courseId, activeMaterialId, onPickMaterial } = useCourseChatCtx();
  return (
    <CourseMaterialList
      courseId={courseId}
      activeMaterialId={activeMaterialId}
      onPickMaterial={onPickMaterial}
    />
  );
}

function MaterialPreviewPanel(_props: IDockviewPanelProps) {
  const { courseId, activeMaterialId, citation } = useCourseChatCtx();
  return (
    <CourseMaterialViewer
      courseId={courseId}
      materialId={activeMaterialId}
      chunkId={citation?.chunkId}
      sourceLabel={citation?.sourceLabel}
    />
  );
}

function ChatPanel(_props: IDockviewPanelProps) {
  const { courseId, hydrateSessionId } = useCourseChatCtx();
  return (
    <ChatComponent courseId={courseId} hydrateSessionId={hydrateSessionId} />
  );
}

const dockviewComponents = {
  materialList: MaterialListPanel,
  materialPreview: MaterialPreviewPanel,
  chat: ChatPanel,
};

type Props = { courseId: string };

export default function CourseChatDockview({ courseId }: Props) {
  const [selectedMaterialId, setSelectedMaterialId] = useState<string | null>(
    null,
  );
  const [citation, setCitation] = useState<CitationPreview | null>(null);
  const [citationTextPanel, setCitationTextPanel] = useState<CitationPreview | null>(null);
  const [hydrateSessionId, setHydrateSessionId] = useState<string | null>(null);
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    setHydrateSessionId(null);
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(
          `/api/v1/courses/${encodeURIComponent(courseId)}/chat/session`,
          { credentials: "include" },
        );
        if (!res.ok || cancelled) return;
        const body = (await res.json()) as { session_id?: string | null };
        const sid =
          typeof body.session_id === "string" && body.session_id.trim()
            ? body.session_id.trim()
            : null;
        if (!cancelled) setHydrateSessionId(sid);
      } catch {
        if (!cancelled) setHydrateSessionId(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [courseId]);

  const apiRef = useRef<DockviewApi | null>(null);
  const layoutDisposableRef = useRef<DockviewIDisposable | null>(null);
  const persistTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resettingLayoutRef = useRef(false);

  const resetDockviewToDefault = useCallback((api: DockviewApi) => {
    const storageKey = dockviewLayoutKey(courseId);
    try {
      localStorage.removeItem(storageKey);
    } catch {
      /* ignore */
    }
    resettingLayoutRef.current = true;
    try {
      api.clear();
      defaultThreeColumnLayout(api);
    } finally {
      resettingLayoutRef.current = false;
    }
  }, [courseId]);

  useEffect(() => {
    const h = (ev: Event) => {
      const ce = ev as CustomEvent<CitationPreview>;
      if (ce.detail?.materialId) {
        setCitation(ce.detail);
        setSelectedMaterialId(ce.detail.materialId);
        setCitationTextPanel(ce.detail);
      }
    };
    window.addEventListener("edu:open-material-preview", h as EventListener);
    return () =>
      window.removeEventListener(
        "edu:open-material-preview",
        h as EventListener,
      );
  }, []);

  useEffect(() => {
    const h = (ev: Event) => {
      const ce = ev as CustomEvent<{ courseId?: string }>;
      if (ce.detail?.courseId !== courseId) return;
      const api = apiRef.current;
      if (!api) return;
      resetDockviewToDefault(api);
    };
    window.addEventListener("edu:reset-course-chat-dockview", h as EventListener);
    return () =>
      window.removeEventListener(
        "edu:reset-course-chat-dockview",
        h as EventListener,
      );
  }, [courseId, resetDockviewToDefault]);

  const activeMaterialId = citation?.materialId ?? selectedMaterialId;

  const onPickMaterial = useCallback((mid: string) => {
    setSelectedMaterialId(mid);
    setCitation(null);
  }, []);

  const ctxValue = useMemo<CourseChatCtx>(
    () => ({
      courseId,
      activeMaterialId,
      onPickMaterial,
      citation,
      hydrateSessionId,
    }),
    [courseId, activeMaterialId, onPickMaterial, citation, hydrateSessionId],
  );

  const dockTheme = resolvedTheme === "dark" ? themeDark : themeLight;

  useEffect(() => {
    const api = apiRef.current;
    if (!api) return;
    api.updateOptions({ theme: dockTheme });
  }, [dockTheme]);

  useEffect(
    () => () => {
      layoutDisposableRef.current?.dispose();
      layoutDisposableRef.current = null;
      if (persistTimerRef.current) {
        clearTimeout(persistTimerRef.current);
        persistTimerRef.current = null;
      }
      apiRef.current = null;
    },
    [courseId],
  );

  const onReady = useCallback(
    (event: DockviewReadyEvent) => {
      const { api } = event;
      apiRef.current = api;

      layoutDisposableRef.current?.dispose();
      layoutDisposableRef.current = null;
      if (persistTimerRef.current) {
        clearTimeout(persistTimerRef.current);
        persistTimerRef.current = null;
      }

      const storageKey = dockviewLayoutKey(courseId);
      let loaded = false;
      try {
        const raw = localStorage.getItem(storageKey);
        if (raw) {
          const parsed = JSON.parse(raw) as unknown;
          if (isPersistedDockviewLayout(parsed)) {
            try {
              api.fromJSON(parsed);
              loaded = true;
            } catch {
              try {
                localStorage.removeItem(storageKey);
              } catch {
                /* ignore */
              }
            }
          }
        }
      } catch {
        try {
          localStorage.removeItem(storageKey);
        } catch {
          /* ignore */
        }
      }

      if (!loaded) {
        api.clear();
        defaultThreeColumnLayout(api);
      } else if (api.panels.length === 0) {
        resetDockviewToDefault(api);
      }

      const schedulePersist = () => {
        if (persistTimerRef.current) clearTimeout(persistTimerRef.current);
        persistTimerRef.current = setTimeout(() => {
          persistTimerRef.current = null;
          if (api.totalPanels === 0) {
            try {
              localStorage.removeItem(storageKey);
            } catch {
              /* ignore */
            }
            return;
          }
          try {
            localStorage.setItem(storageKey, JSON.stringify(api.toJSON()));
          } catch {
            /* ignore */
          }
        }, 400);
      };

      const layoutChangeSub = api.onDidLayoutChange(schedulePersist);
      const removePanelSub = api.onDidRemovePanel(() => {
        if (resettingLayoutRef.current) return;
        if (api.panels.length === 0) {
          resetDockviewToDefault(api);
        }
      });
      layoutDisposableRef.current = {
        dispose() {
          layoutChangeSub.dispose();
          removePanelSub.dispose();
        },
      };
    },
    [courseId, resetDockviewToDefault],
  );

  return (
    <CourseChatCtx.Provider value={ctxValue}>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="flex flex-1 min-h-0 min-w-0">
          <DockviewReact
            key={courseId}
            className="h-full min-h-0 flex-1"
            theme={dockTheme}
            components={dockviewComponents}
            onReady={onReady}
          />

          {/* Citation text panel — slides in from right alongside dockview */}
          <aside
            className={cn(
              "flex flex-col border-l border-border bg-background transition-[width,opacity] duration-200 ease-out shrink-0 overflow-hidden",
              citationTextPanel
                ? "w-[min(420px,44vw)] min-w-[280px] opacity-100"
                : "w-0 min-w-0 opacity-0",
            )}
          >
            {citationTextPanel && (
              <>
                <div className="flex items-center gap-2 px-3 py-2 border-b border-border shrink-0 bg-muted/30">
                  <span className="text-xs font-medium text-muted-foreground truncate flex-1">
                    {citationTextPanel.sourceLabel ?? "引用资料"}
                  </span>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7 shrink-0"
                    aria-label="关闭引用面板"
                    onClick={() => setCitationTextPanel(null)}
                  >
                    <X size={15} />
                  </Button>
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto">
                  {citationTextPanel.chunkText ? (
                    <div className="p-4 space-y-4">
                      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <FileText size={13} />
                        <span>检索文本块</span>
                      </div>
                      <div className="prose prose-sm dark:prose-invert max-w-none rounded-lg bg-muted/40 border border-border p-3 text-xs leading-relaxed">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {citationTextPanel.chunkText}
                        </ReactMarkdown>
                      </div>
                      {(citationTextPanel.image_urls?.length ?? 0) > 0 && (
                        <div className="space-y-2">
                          <p className="text-xs text-muted-foreground">相关图片</p>
                          <div className="flex flex-col gap-3">
                            {citationTextPanel.image_urls!.map((img, idx) => (
                              <div key={idx} className="space-y-1">
                                <p className="text-[10px] text-muted-foreground">第 {img.page_idx + 1} 页</p>
                                <Image
                                  src={img.url}
                                  alt={`第 ${img.page_idx + 1} 页`}
                                  width={0}
                                  height={0}
                                  unoptimized
                                  style={{ width: "100%", height: "auto" }}
                                  className="rounded border border-border object-contain"
                                />
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
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
    </CourseChatCtx.Provider>
  );
}
