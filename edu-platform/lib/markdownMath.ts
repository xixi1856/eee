import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

export const markdownRemarkPlugins = [remarkGfm, remarkMath];
export const markdownRehypePlugins = [rehypeKatex];

export function normalizeMathDelimiters(text: string): string {
  return text
    .replace(/\\{1,2}\[/g, () => "$$")
    .replace(/\\{1,2}\]/g, () => "$$")
    .replace(/\\{1,2}\(/g, "$")
    .replace(/\\{1,2}\)/g, "$");
}