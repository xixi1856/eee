import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryConsolidator } from "@/lib/agent/memory/memory-consolidator";

describe("MemoryConsolidator", () => {
  const store = {
    addFact: vi.fn(),
    listFacts: vi.fn(),
    listConcepts: vi.fn(),
    saveConcept: vi.fn(),
    loadProfile: vi.fn(),
    saveProfile: vi.fn(),
  };

  const extractor = {
    extractFactsFromSession: vi.fn(),
  };

  beforeEach(() => {
    Object.values(store).forEach((fn) => fn.mockReset());
    extractor.extractFactsFromSession.mockReset();
  });

  it("业务规则：无新事实时不应触发概念聚合与画像更新", async () => {
    // given
    extractor.extractFactsFromSession.mockResolvedValue([]);
    const consolidator = new MemoryConsolidator(store as never, extractor as never);

    // when
    await consolidator.consolidateSession("u-1", "s-1", [
      { role: "user", content: "你好" },
      { role: "assistant", content: "你好" },
    ]);

    // then
    expect(store.saveConcept).not.toHaveBeenCalled();
    expect(store.saveProfile).not.toHaveBeenCalled();
  });

  it("业务规则：应按 mastery/confusion 置信度更新概念掌握度并刷新画像", async () => {
    // given
    extractor.extractFactsFromSession.mockResolvedValue([
      {
        userId: "u-1",
        sessionId: "s-1",
        timestamp: new Date("2026-01-01"),
        category: "concept_mastery",
        content: "TCP",
        confidence: 0.9,
        sourceJson: { session_id: "s-1" },
        metadata: {},
      },
    ]);

    store.listFacts.mockResolvedValue([
      {
        id: "f-1",
        userId: "u-1",
        sessionId: "s-1",
        timestamp: new Date("2026-01-01"),
        category: "concept_mastery",
        content: "TCP",
        confidence: 0.9,
        sourceJson: { session_id: "s-1" },
        metadata: {},
      },
      {
        id: "f-2",
        userId: "u-1",
        sessionId: "s-1",
        timestamp: new Date("2026-01-01"),
        category: "concept_confusion",
        content: "TCP",
        confidence: 0.7,
        sourceJson: { session_id: "s-1" },
        metadata: {},
      },
      {
        id: "f-low",
        userId: "u-1",
        sessionId: "s-1",
        timestamp: new Date("2026-01-01"),
        category: "concept_confusion",
        content: "TCP",
        confidence: 0.2,
        sourceJson: { session_id: "s-1" },
        metadata: {},
      },
    ]);

    store.listConcepts.mockResolvedValue([
      {
        id: "c-1",
        userId: "u-1",
        name: "TCP",
        description: "",
        masteryLevel: 0.5,
        lastUpdated: new Date("2026-01-01"),
        supportingFactIds: [],
        relatedConcepts: [],
        metadata: {},
      },
    ]);
    store.loadProfile.mockResolvedValue({ userId: "u-1", profile: { grade: "G9" } });

    const consolidator = new MemoryConsolidator(store as never, extractor as never);

    // when
    await consolidator.consolidateSession("u-1", "s-1", [
      { role: "user", content: "解释 TCP" },
      { role: "assistant", content: "解释完成" },
    ]);

    // then
    const [conceptPayload] = store.saveConcept.mock.calls[0] as [
      { name: string; masteryLevel: number; supportingFactIds: string[] },
    ];
    expect(conceptPayload.name).toBe("TCP");
    expect(conceptPayload.masteryLevel).toBeCloseTo(0.555, 3);
    expect(conceptPayload.supportingFactIds).toEqual(["f-1", "f-2"]);

    const [profileUserId, profileData] = store.saveProfile.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ];
    expect(profileUserId).toBe("u-1");
    expect(profileData.last_session_id).toBe("s-1");
    expect(Array.isArray(profileData.top_concepts)).toBe(true);
    expect((profileData.top_concepts as string[])[0]).toBe("TCP");
  });
});
