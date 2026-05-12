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
import ChatComponent from "@/components/ChatComponent";
import CourseMaterialList from "@/components/CourseMaterialList";
import CourseMaterialViewer from "@/components/CourseMaterialViewer";

type CitationPreview = {
  materialId: string;
  chunkId?: string;
  sourceLabel?: string;
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

  useEffect(() => {
    const h = (ev: Event) => {
      const ce = ev as CustomEvent<CitationPreview>;
      if (ce.detail?.materialId) {
        setCitation(ce.detail);
        setSelectedMaterialId(ce.detail.materialId);
      }
    };
    window.addEventListener("edu:open-material-preview", h as EventListener);
    return () =>
      window.removeEventListener(
        "edu:open-material-preview",
        h as EventListener,
      );
  }, []);

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
      }

      const schedulePersist = () => {
        if (persistTimerRef.current) clearTimeout(persistTimerRef.current);
        persistTimerRef.current = setTimeout(() => {
          persistTimerRef.current = null;
          try {
            localStorage.setItem(storageKey, JSON.stringify(api.toJSON()));
          } catch {
            /* ignore */
          }
        }, 400);
      };

      layoutDisposableRef.current = api.onDidLayoutChange(schedulePersist);
    },
    [courseId],
  );

  return (
    <CourseChatCtx.Provider value={ctxValue}>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <DockviewReact
          key={courseId}
          className="h-full min-h-0 w-full flex-1"
          theme={dockTheme}
          components={dockviewComponents}
          onReady={onReady}
        />
      </div>
    </CourseChatCtx.Provider>
  );
}
