import { prisma } from "@/lib/db";
import { ApiError } from "@/lib/http/api-error";
import { createAgentSession } from "@/lib/agentClient";

/** Prisma client may lag schema until ``npx prisma generate`` succeeds on Windows. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const px = prisma as any;

export type ChatThreadKind = "course" | "global";

export type ChatThreadListItem = {
  session_id: string;
  kind: ChatThreadKind;
  course_id: string | null;
  course_name: string | null;
  title: string;
  last_message_at: string;
  has_messages: boolean;
};

function clip(s: string, n: number): string {
  const t = s.trim();
  if (t.length <= n) return t;
  return `${t.slice(0, n)}…`;
}

type QcSessionRow = {
  id: string;
  agentSessionId: string;
  title: string | null;
  updatedAt: Date;
};

type TitleOverrideRow = { sessionId: string; title: string };

export async function listChatThreads(
  studentId: string,
): Promise<ChatThreadListItem[]> {
  const withLogs = await prisma.qaLog.groupBy({
    by: ["sessionId"],
    where: {
      studentId,
      deletedAt: null,
      answer: { not: null },
    },
    _max: { createdAt: true },
  });

  const qcSessions = (await px.qaCenterSession.findMany({
    where: { studentId, deletedAt: null },
    orderBy: { updatedAt: "desc" },
  })) as QcSessionRow[];

  const sessionIdsFromLogs = new Set(withLogs.map((g) => g.sessionId));
  const allSessionIds = new Set<string>([
    ...sessionIdsFromLogs,
    ...qcSessions.map((q) => q.agentSessionId),
  ]);

  const ccsRows =
    allSessionIds.size > 0
      ? await prisma.courseChatSession.findMany({
          where: { agentSessionId: { in: [...allSessionIds] } },
          include: { course: { select: { id: true, name: true } } },
        })
      : [];
  const ccsBySid = new Map(ccsRows.map((r) => [r.agentSessionId, r]));

  const overrides: TitleOverrideRow[] =
    allSessionIds.size > 0
      ? ((await px.chatThreadTitleOverride.findMany({
          where: {
            studentId,
            sessionId: { in: [...allSessionIds] },
          },
        })) as TitleOverrideRow[])
      : [];
  const overrideBySid = new Map<string, string>(
    overrides.map((o) => [o.sessionId, o.title]),
  );

  const ids = [...allSessionIds];
  const firstQBySid = new Map<string, string>();
  if (ids.length > 0) {
    const logs = await prisma.qaLog.findMany({
      where: { studentId, deletedAt: null, sessionId: { in: ids } },
      orderBy: { createdAt: "asc" },
      select: { sessionId: true, question: true },
    });
    for (const row of logs) {
      if (!firstQBySid.has(row.sessionId)) {
        firstQBySid.set(row.sessionId, row.question);
      }
    }
  }

  const items: ChatThreadListItem[] = [];

  for (const g of withLogs) {
    const sid = g.sessionId;
    const ccs = ccsBySid.get(sid);
    const qcs = qcSessions.find((q) => q.agentSessionId === sid);
    const kind: ChatThreadKind = qcs ? "global" : "course";
    const courseId = ccs?.courseId ?? null;
    const courseName = ccs?.course.name ?? null;
    const qcsTitle =
      qcs && typeof qcs.title === "string" && qcs.title.trim() ? qcs.title.trim() : "";
    const title =
      kind === "global" && qcsTitle
        ? qcsTitle
        : overrideBySid.get(sid)?.trim() ||
          (firstQBySid.get(sid) ? clip(firstQBySid.get(sid)!, 48) : null) ||
          (courseName ? `课程：${courseName}` : "新对话");
    items.push({
      session_id: sid,
      kind,
      course_id: courseId,
      course_name: courseName,
      title,
      last_message_at: (g._max.createdAt ?? new Date(0)).toISOString(),
      has_messages: true,
    });
  }

  for (const q of qcSessions) {
    if (sessionIdsFromLogs.has(q.agentSessionId)) continue;
    const qt = typeof q.title === "string" && q.title.trim() ? q.title.trim() : "";
    const title =
      qt ||
      overrideBySid.get(q.agentSessionId)?.trim() ||
      "新对话";
    items.push({
      session_id: q.agentSessionId,
      kind: "global",
      course_id: null,
      course_name: null,
      title,
      last_message_at: q.updatedAt.toISOString(),
      has_messages: false,
    });
  }

  items.sort(
    (a, b) =>
      new Date(b.last_message_at).getTime() -
      new Date(a.last_message_at).getTime(),
  );
  return items;
}

export type ChatThreadMessage = {
  id: string;
  question: string;
  answer: string | null;
  created_at: string;
  tool_calls: unknown[];
  citations: unknown[];
};

export async function assertThreadAccess(
  sessionId: string,
  studentId: string,
): Promise<{ kind: ChatThreadKind; courseId: string | null }> {
  const qcs = await px.qaCenterSession.findFirst({
    where: { agentSessionId: sessionId, studentId, deletedAt: null },
  });
  if (qcs) return { kind: "global", courseId: null };

  const ccs = await prisma.courseChatSession.findFirst({
    where: { agentSessionId: sessionId, studentId },
  });
  if (ccs) return { kind: "course", courseId: ccs.courseId };

  const log = await prisma.qaLog.findFirst({
    where: { sessionId, studentId, deletedAt: null },
    select: { courseId: true },
  });
  if (log) {
    return {
      kind: log.courseId ? "course" : "global",
      courseId: log.courseId,
    };
  }

  throw new ApiError(404, "NOT_FOUND", "会话不存在");
}

export async function getThreadMessages(
  sessionId: string,
  studentId: string,
): Promise<ChatThreadMessage[]> {
  await assertThreadAccess(sessionId, studentId);
  const rows = await prisma.qaLog.findMany({
    where: { sessionId, studentId, deletedAt: null },
    orderBy: { createdAt: "asc" },
    select: {
      id: true,
      question: true,
      answer: true,
      createdAt: true,
      toolCalls: true,
      citations: true,
    },
  });
  return rows.map((r) => ({
    id: r.id,
    question: r.question,
    answer: r.answer,
    created_at: r.createdAt.toISOString(),
    tool_calls: Array.isArray(r.toolCalls) ? r.toolCalls : [],
    citations: Array.isArray(r.citations) ? r.citations : [],
  }));
}

export async function updateThreadTitle(
  sessionId: string,
  studentId: string,
  title: string,
): Promise<void> {
  const t = title.trim();
  if (!t) {
    throw new ApiError(400, "VALIDATION_ERROR", "title is required");
  }
  if (t.length > 200) {
    throw new ApiError(400, "VALIDATION_ERROR", "title too long");
  }
  await assertThreadAccess(sessionId, studentId);

  const qcs = await px.qaCenterSession.findFirst({
    where: { agentSessionId: sessionId, studentId, deletedAt: null },
  });
  if (qcs) {
    await px.qaCenterSession.update({
      where: { id: qcs.id },
      data: { title: t },
    });
    return;
  }

  await px.chatThreadTitleOverride.upsert({
    where: {
      studentId_sessionId: { studentId, sessionId },
    },
    create: { studentId, sessionId, title: t },
    update: { title: t },
  });
}

export async function softDeleteThread(
  sessionId: string,
  studentId: string,
): Promise<void> {
  await assertThreadAccess(sessionId, studentId);
  const now = new Date();
  await prisma.$transaction([
    prisma.qaLog.updateMany({
      where: { sessionId, studentId, deletedAt: null },
      data: { deletedAt: now },
    }),
    px.qaCenterSession.updateMany({
      where: { agentSessionId: sessionId, studentId, deletedAt: null },
      data: { deletedAt: now },
    }),
    px.chatThreadTitleOverride.deleteMany({
      where: { studentId, sessionId },
    }),
  ]);
}

export async function createEmptyGlobalThread(
  studentId: string,
  agentUserId: string,
): Promise<{ session_id: string }> {
  const agentSessionId = await createAgentSession(agentUserId, "问答中心");
  await px.qaCenterSession.create({
    data: { studentId, agentSessionId },
  });
  return { session_id: agentSessionId };
}
