import { describe, it, expect } from "vitest";

import { normalizeMathDelimiters } from "@/lib/markdownMath";

describe("normalizeMathDelimiters", () => {
  it("normalizes single-backslash LaTeX delimiters", () => {
    const input = "inline: \\(x^2 + y^2\\), block: \\[x+y\\]";
    const output = normalizeMathDelimiters(input);

    expect(output).toBe("inline: $x^2 + y^2$, block: $$x+y$$");
  });

  it("normalizes double-backslash escaped delimiters", () => {
    const input = "inline: \\\\(x+y\\\\), block: \\\\[x-y\\\\]";
    const output = normalizeMathDelimiters(input);

    expect(output).toBe("inline: $x+y$, block: $$x-y$$");
  });

  it("keeps plain markdown math delimiters unchanged", () => {
    const input = "already inline: $a+b$, already block: $$c+d$$";
    const output = normalizeMathDelimiters(input);

    expect(output).toBe(input);
  });
});
