"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  horizontalListSortingStrategy,
  useSortable,
  sortableKeyboardCoordinates,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  GripVertical,
  LayoutGrid,
  Rows3,
} from "lucide-react";
import {
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";
import { Button } from "@/components/ui/button";
import ChatComponent from "@/components/ChatComponent";
import CourseMaterialList from "@/components/CourseMaterialList";
import CourseMaterialViewer from "@/components/CourseMaterialViewer";

const MODULE_IDS = ["list", "preview", "chat"] as const;
export type CourseChatModuleId = (typeof MODULE_IDS)[number];

function moduleOrderStorageKey(courseId: string): string {
  return `edu:course-chat:module-order:${courseId}`;
}

function parseModuleOrder(raw: unknown): CourseChatModuleId[] | null {
  if (!Array.isArray(raw) || raw.length !== 3) return null;
  const set = new Set(raw);
  if (set.size !== 3) return null;
  for (const id of MODULE_IDS) {
    if (!set.has(id)) return null;
  }
  return raw as CourseChatModuleId[];
}

function SortableModuleChip({
  id,
  label,
}: {
  id: CourseChatModuleId;
  label: string;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.88 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="flex items-center gap-1 rounded-lg border border-border bg-muted/50 px-2 py-1 text-xs shadow-sm"
    >
      <button
        type="button"
        className="touch-none rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground cursor-grab active:cursor-grabbing"
        {...attributes}
        {...listeners}
      >
        <GripVertical size={14} aria-hidden />
      </button>
      <span className="font-medium pr-0.5 select-none">{label}</span>
    </div>
  );
}

function moduleLabel(id: CourseChatModuleId): string {
  if (id === "list") return "资料列表";
  if (id === "preview") return "资料预览";
  return "课程问答";
}

type CitationPreview = {
  materialId: string;
  chunkId?: string;
  sourceLabel?: string;
};

type Props = {
  courseId: string;
};

export default function CourseChatWorkspace({ courseId }: Props) {
  const [selectedMaterialId, setSelectedMaterialId] = useState<string | null>(
    null,
  );
  const [citation, setCitation] = useState<CitationPreview | null>(null);
  const [moduleOrder, setModuleOrder] = useState<CourseChatModuleId[]>([
    "list",
    "preview",
    "chat",
  ]);
  const [layoutVertical, setLayoutVertical] = useState(false);

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
      window.removeEventListener("edu:open-material-preview", h as EventListener);
  }, []);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(moduleOrderStorageKey(courseId));
      const parsed = raw ? JSON.parse(raw) : null;
      const next = parseModuleOrder(parsed);
      if (next) setModuleOrder(next);
    } catch {
      /* ignore */
    }
  }, [courseId]);

  const activeMaterialId = citation?.materialId ?? selectedMaterialId;

  const persistOrder = useCallback(
    (next: CourseChatModuleId[]) => {
      setModuleOrder(next);
      try {
        localStorage.setItem(
          moduleOrderStorageKey(courseId),
          JSON.stringify(next),
        );
      } catch {
        /* ignore */
      }
    },
    [courseId],
  );

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 8 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;
      const oldIndex = moduleOrder.indexOf(active.id as CourseChatModuleId);
      const newIndex = moduleOrder.indexOf(over.id as CourseChatModuleId);
      if (oldIndex < 0 || newIndex < 0) return;
      persistOrder(arrayMove(moduleOrder, oldIndex, newIndex));
    },
    [moduleOrder, persistOrder],
  );

  const panelGroupDirection = layoutVertical ? "vertical" : "horizontal";
  const resizeHandleClass = layoutVertical
    ? "h-1.5 bg-border hover:bg-primary/25 shrink-0 data-[panel-resize-handle-active]:bg-primary/35"
    : "w-1.5 bg-border hover:bg-primary/25 shrink-0 data-[panel-resize-handle-active]:bg-primary/35";

  const renderPanel = useCallback(
    (id: CourseChatModuleId) => {
      if (id === "list") {
        return (
          <CourseMaterialList
            courseId={courseId}
            activeMaterialId={activeMaterialId}
            onPickMaterial={(mid) => {
              setSelectedMaterialId(mid);
              setCitation(null);
            }}
          />
        );
      }
      if (id === "preview") {
        return (
          <CourseMaterialViewer
            courseId={courseId}
            materialId={activeMaterialId}
            chunkId={citation?.chunkId}
            sourceLabel={citation?.sourceLabel}
          />
        );
      }
      return <ChatComponent courseId={courseId} />;
    },
    [courseId, activeMaterialId, citation],
  );

  const panels = useMemo(
    () =>
      moduleOrder.flatMap((id, index) => {
        const nodes: ReactNode[] = [
          <Panel
            key={id}
            id={id}
            defaultSize={id === "list" ? 24 : id === "preview" ? 36 : 40}
            minSize={12}
            collapsible
            className="min-h-0 min-w-0 flex flex-col overflow-hidden"
          >
            {renderPanel(id)}
          </Panel>,
        ];
        if (index < moduleOrder.length - 1) {
          nodes.push(
            <PanelResizeHandle
              key={`handle-${id}-${moduleOrder[index + 1]}`}
              className={resizeHandleClass}
            />,
          );
        }
        return nodes;
      }),
    [moduleOrder, renderPanel, resizeHandleClass],
  );

  return (
    <div className="flex flex-1 min-h-0 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/20 px-2 py-1.5 shrink-0">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 gap-1 text-[11px]"
          onClick={() => setLayoutVertical((v) => !v)}
        >
          {layoutVertical ? (
            <>
              <LayoutGrid size={14} />
              横向分栏
            </>
          ) : (
            <>
              <Rows3 size={14} />
              纵向分栏
            </>
          )}
        </Button>
        <span className="text-[10px] text-muted-foreground uppercase tracking-wide">
          模块顺序（拖握柄）
        </span>
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={onDragEnd}
        >
          <SortableContext
            items={[...moduleOrder]}
            strategy={horizontalListSortingStrategy}
          >
            <div className="flex flex-wrap items-center gap-2">
              {moduleOrder.map((id) => (
                <SortableModuleChip
                  key={id}
                  id={id}
                  label={moduleLabel(id)}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      </div>

      <PanelGroup
        key={panelGroupDirection}
        direction={panelGroupDirection}
        autoSaveId={`edu-course-chat-panels-${courseId}-${panelGroupDirection}`}
        className="flex-1 min-h-0"
      >
        {panels}
      </PanelGroup>
    </div>
  );
}
