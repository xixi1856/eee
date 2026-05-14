"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ChevronLeft, Plus, Pencil, Trash2, X, CheckCircle2, AlertCircle, GripVertical } from "lucide-react";
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
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type Lesson = {
  id: string;
  course_id: string;
  title: string;
  description: string | null;
  order_index: number;
  created_at: string;
  updated_at: string;
};

function useNotify() {
  const [n, setN] = useState<{ type: "success" | "error"; msg: string } | null>(null);
  function notify(type: "success" | "error", msg: string) {
    setN({ type, msg });
    setTimeout(() => setN(null), 3000);
  }
  return { notification: n, notify };
}

function Modal({
  open, title, onClose, children,
}: { open: boolean; title: string; onClose: () => void; children: React.ReactNode }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-md rounded-2xl border border-border bg-card shadow-xl p-6 mx-4">
        <div className="flex items-center justify-between mb-5">
          <h3 className="font-display text-base font-semibold text-foreground">{title}</h3>
          <button onClick={onClose} className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted transition-colors">
            <X size={14} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function SortableLessonRow({
  lesson: l,
  isTeacher,
  onEdit,
  onDelete,
}: {
  lesson: Lesson;
  isTeacher: boolean;
  onEdit: (l: Lesson) => void;
  onDelete: (id: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: l.id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 10 : undefined,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        "flex items-start justify-between gap-3 rounded-xl border border-border bg-card px-4 py-3 transition-colors",
        isDragging ? "shadow-lg border-primary/40 opacity-90" : "hover:border-border/60",
      )}
    >
      <div className="flex items-start gap-3 min-w-0">
        {isTeacher && (
          <button
            {...listeners}
            {...attributes}
            className="shrink-0 flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:text-foreground cursor-grab active:cursor-grabbing mt-0.5 touch-none"
            aria-label="拖拽排序"
          >
            <GripVertical size={15} />
          </button>
        )}
        <span className="shrink-0 flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 text-primary text-xs font-bold mt-0.5">
          {l.order_index}
        </span>
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground truncate">{l.title}</p>
          {l.description && <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{l.description}</p>}
        </div>
      </div>
      {isTeacher && (
        <div className="flex items-center gap-1 shrink-0">
          <button onClick={() => onEdit(l)} className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground transition-colors">
            <Pencil size={13} />
          </button>
          <button onClick={() => onDelete(l.id)} className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors">
            <Trash2 size={13} />
          </button>
        </div>
      )}
    </div>
  );
}

export default function CourseLessonsPage() {
  const params = useParams();
  const courseId = typeof params?.courseId === "string" ? params.courseId : null;
  const [rows, setRows] = useState<Lesson[]>([]);
  const [loading, setLoading] = useState(true);
  const [role, setRole] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Lesson | null>(null);
  const [saving, setSaving] = useState(false);
  const [reordering, setReordering] = useState(false);
  const [formTitle, setFormTitle] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [formOrder, setFormOrder] = useState(1);
  const { notification, notify } = useNotify();
  const prevRowsRef = useRef<Lesson[]>([]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const load = useCallback(async () => {
    if (!courseId) return;
    setLoading(true);
    try {
      const [lessonRes, userRes] = await Promise.all([
        fetch(`/api/v1/courses/${courseId}/lessons`, { credentials: "include" }),
        fetch("/api/v1/user", { credentials: "include" }),
      ]);
      if (userRes.ok) {
        const u = (await userRes.json()) as { role?: string };
        setRole(u.role ?? null);
      }
      if (lessonRes.ok) {
        const j = (await lessonRes.json()) as { lessons?: Lesson[] };
        setRows((j.lessons ?? []).sort((a, b) => a.order_index - b.order_index));
      } else {
        setRows([]);
        notify("error", "加载课时失败");
      }
    } finally {
      setLoading(false);
    }
  }, [courseId]);

  useEffect(() => { void load(); }, [load]);

  const isTeacher = role === "TEACHER";
  const nextOrder = useMemo(() => rows.length === 0 ? 1 : Math.max(...rows.map(x => x.order_index)) + 1, [rows]);

  function openCreate() {
    setEditing(null);
    setFormTitle(""); setFormDesc(""); setFormOrder(nextOrder);
    setOpen(true);
  }

  function openEdit(row: Lesson) {
    setEditing(row);
    setFormTitle(row.title); setFormDesc(row.description ?? ""); setFormOrder(row.order_index);
    setOpen(true);
  }

  async function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id || !courseId) return;
    const oldIndex = rows.findIndex((r) => r.id === active.id);
    const newIndex = rows.findIndex((r) => r.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;
    const reordered = arrayMove(rows, oldIndex, newIndex).map((r, i) => ({
      ...r,
      order_index: i + 1,
    }));
    prevRowsRef.current = rows;
    setRows(reordered);
    setReordering(true);
    try {
      const res = await fetch(`/api/v1/courses/${courseId}/lessons`, {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orders: reordered.map((r) => ({ id: r.id, order_index: r.order_index })) }),
      });
      if (!res.ok) {
        setRows(prevRowsRef.current);
        notify("error", "排序保存失败，已恢复原顺序");
      } else {
        notify("success", "课时顺序已保存");
      }
    } catch {
      setRows(prevRowsRef.current);
      notify("error", "排序保存失败，已恢复原顺序");
    } finally {
      setReordering(false);
    }
  }

  async function submitForm() {
    if (!courseId || !formTitle.trim()) { notify("error", "课时标题不能为空"); return; }
    setSaving(true);
    try {
      const payload = { title: formTitle.trim(), description: formDesc.trim() || null, order_index: formOrder };
      const url = editing ? `/api/v1/courses/${courseId}/lessons/${editing.id}` : `/api/v1/courses/${courseId}/lessons`;
      const method = editing ? "PATCH" : "POST";
      const res = await fetch(url, {
        method, credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const j = (await res.json().catch(() => ({}))) as { error?: { message?: string } };
        notify("error", j.error?.message ?? "保存失败"); return;
      }
      notify("success", editing ? "课时已更新" : "课时已创建");
      setOpen(false);
      void load();
    } finally {
      setSaving(false);
    }
  }

  async function removeLesson(lessonId: string) {
    if (!courseId || !window.confirm("确定删除该课时？此操作不可撤销。")) return;
    const res = await fetch(`/api/v1/courses/${courseId}/lessons/${lessonId}`, {
      method: "DELETE", credentials: "include",
    });
    if (res.ok) { notify("success", "课时已删除"); void load(); }
    else notify("error", "删除失败");
  }

  if (!courseId) return <div className="flex items-center justify-center h-full text-muted-foreground text-sm">无效的课程链接</div>;

  return (
    <div className="flex flex-col h-full overflow-auto">
      {notification && (
        <div className={cn(
          "fixed top-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium shadow-lg",
          notification.type === "success" ? "bg-[oklch(0.92_0.08_145)] text-[oklch(0.35_0.10_145)]" : "bg-destructive text-destructive-foreground"
        )}>
          {notification.type === "success" ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
          {notification.msg}
        </div>
      )}

      <div className="max-w-3xl mx-auto w-full px-6 py-8 space-y-6">
        <Link href={`/courses/${courseId}`} className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <ChevronLeft size={15} />
          返回课程详情
        </Link>

        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground">课时管理</h1>
            <p className="mt-1 text-sm text-muted-foreground">共 {rows.length} 个课时，按序号排列</p>
          </div>
          {isTeacher && <Button size="sm" onClick={openCreate}><Plus size={14} className="mr-1.5" />新建课时</Button>}
        </div>

        {loading ? (
          <div className="space-y-2">
            {Array.from({length:4}).map((_,i)=><Skeleton key={i} className="h-14 w-full rounded-xl" />)}
          </div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 rounded-xl border border-dashed border-border text-center">
            <p className="text-sm font-medium text-foreground mb-1">暂无课时</p>
            {isTeacher && <p className="text-xs text-muted-foreground">点击「新建课时」开始添加</p>}
          </div>
        ) : (
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={(e) => void handleDragEnd(e)}>
            <SortableContext items={rows.map((r) => r.id)} strategy={verticalListSortingStrategy}>
              <div className={cn("space-y-2", reordering && "opacity-60 pointer-events-none")}>
                {rows.map((l) => (
                  <SortableLessonRow
                    key={l.id}
                    lesson={l}
                    isTeacher={isTeacher}
                    onEdit={openEdit}
                    onDelete={(id) => void removeLesson(id)}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        )}
      </div>

      <Modal open={open} title={editing ? "编辑课时" : "新建课时"} onClose={() => setOpen(false)}>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-foreground">课时标题 <span className="text-destructive">*</span></label>
            <Input placeholder="例：第一章 · 概述" value={formTitle} onChange={e => setFormTitle(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-foreground">课时描述</label>
            <Textarea rows={3} placeholder="选填" value={formDesc} onChange={e => setFormDesc(e.target.value)} className="resize-none" />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-foreground">序号</label>
            <Input type="number" min={1} value={formOrder} onChange={e => setFormOrder(Number(e.target.value))} />
          </div>
          <div className="flex items-center gap-2 pt-1">
            <Button onClick={() => void submitForm()} disabled={saving} className="flex-1">
              {saving ? "保存中…" : editing ? "更新" : "创建"}
            </Button>
            <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
