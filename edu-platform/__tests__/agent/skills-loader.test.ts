import { describe, it, expect, afterEach } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { SkillsLoader } from "@/lib/agent/skills-loader";

const tempDirs: string[] = [];

function createTempSkillsDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "skills-loader-"));
  tempDirs.push(dir);
  return dir;
}

afterEach(() => {
  while (tempDirs.length > 0) {
    const dir = tempDirs.pop();
    if (dir) fs.rmSync(dir, { recursive: true, force: true });
  }
});

describe("SkillsLoader", () => {
  it("业务规则：技能目录不存在时应返回空列表", () => {
    // given
    const loader = new SkillsLoader(path.join(os.tmpdir(), "not-exist-skills-dir"));

    // when
    const skills = loader.load();

    // then
    expect(skills).toEqual([]);
  });

  it("业务规则：应解析 frontmatter 并保留 always_inject", () => {
    // given
    const dir = createTempSkillsDir();
    fs.writeFileSync(
      path.join(dir, "socratic.md"),
      [
        "---",
        "name: Socratic",
        "description: 启发式提问",
        "version: 2.1.0",
        "always_inject: true",
        "---",
        "请先提问，再解释。",
      ].join("\n"),
      "utf-8",
    );
    const loader = new SkillsLoader(dir);

    // when
    const skills = loader.load();

    // then
    expect(skills).toHaveLength(1);
    expect(skills[0]).toMatchObject({
      name: "Socratic",
      description: "启发式提问",
      version: "2.1.0",
      alwaysInject: true,
    });
    expect(skills[0]?.body).toContain("请先提问");
  });

  it("业务规则：目录型技能应覆盖同名平铺 md 技能", () => {
    // given
    const dir = createTempSkillsDir();
    const folder = path.join(dir, "planner");
    fs.mkdirSync(folder);
    fs.writeFileSync(
      path.join(folder, "SKILL.md"),
      [
        "---",
        "name: Planner",
        "description: 目录版本",
        "---",
        "目录技能正文",
      ].join("\n"),
      "utf-8",
    );
    fs.writeFileSync(
      path.join(dir, "planner.md"),
      [
        "---",
        "name: Planner",
        "description: 平铺版本",
        "---",
        "平铺技能正文",
      ].join("\n"),
      "utf-8",
    );

    const loader = new SkillsLoader(dir);

    // when
    const skills = loader.load();
    const body = loader.getBody("Planner");

    // then
    expect(skills).toHaveLength(1);
    expect(skills[0]?.description).toBe("目录版本");
    expect(body).toContain("目录技能正文");
    expect(body).not.toContain("平铺技能正文");
  });
});
