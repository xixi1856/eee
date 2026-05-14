import { describe, expect, it, vi, beforeEach } from "vitest";

const { mockTransaction } = vi.hoisted(() => ({
  mockTransaction: vi.fn(),
}));

vi.mock("@/lib/db", () => ({
  prisma: {
    $transaction: mockTransaction,
  },
}));

describe("changePassword", () => {
  beforeEach(() => {
    mockTransaction.mockReset();
  });

  it("rejects when new password equals current without calling DB transaction", async () => {
    const { changePassword } = await import("@/lib/services/authService");
    await expect(
      changePassword("00000000-0000-4000-8000-000000000001", {
        current_password: "sameValue",
        new_password: "sameValue",
      }),
    ).rejects.toMatchObject({
      code: "VALIDATION_ERROR",
      message: "New password must differ from current password",
    });
    expect(mockTransaction).not.toHaveBeenCalled();
  });
});
