import html2canvas from "html2canvas";

/** Dispatched with `detail: { file: File }` so `ChatComponent` can call `addAttachment`. */
export const EDU_CHAT_ADD_ATTACHMENT_EVENT = "edu:chat-add-attachment" as const;

const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;
const TARGET_MAX_BYTES = 18 * 1024 * 1024;
const MAX_EDGE = 4096;

function scaleCanvasMaxEdge(source: HTMLCanvasElement, maxEdge: number): HTMLCanvasElement {
  let w = source.width;
  let h = source.height;
  if (w <= maxEdge && h <= maxEdge) return source;
  const r = Math.min(maxEdge / w, maxEdge / h);
  w = Math.floor(w * r);
  h = Math.floor(h * r);
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d");
  if (!ctx) return source;
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(source, 0, 0, w, h);
  return c;
}

function blobFromCanvas(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("无法生成 PNG"))),
      "image/png",
    );
  });
}

/**
 * Rasterizes the **visible viewport** of a scrollable element to a PNG `File`,
 * downsampling so size stays under the chat attachment limit.
 */
export async function captureScrollViewportToPngFile(
  element: HTMLElement,
  filename: string,
): Promise<File> {
  if (!element.clientWidth || !element.clientHeight) {
    throw new Error("预览区域尺寸无效，请稍后再试。");
  }

  const scale = Math.min(2, window.devicePixelRatio || 1);

  const canvas = await html2canvas(element, {
    scale,
    useCORS: true,
    allowTaint: true,
    logging: false,
    width: element.clientWidth,
    height: element.clientHeight,
    windowWidth: element.clientWidth,
    windowHeight: element.clientHeight,
    scrollX: -element.scrollLeft,
    scrollY: -element.scrollTop,
    backgroundColor: null,
  });

  let working = scaleCanvasMaxEdge(canvas, MAX_EDGE);
  let blob = await blobFromCanvas(working);

  let iterations = 0;
  while (blob.size > TARGET_MAX_BYTES && iterations < 10) {
    iterations += 1;
    const w = Math.max(320, Math.floor(working.width * 0.85));
    const h = Math.max(240, Math.floor(working.height * 0.85));
    if (w === working.width && h === working.height) break;
    const next = document.createElement("canvas");
    next.width = w;
    next.height = h;
    const ctx = next.getContext("2d");
    if (!ctx) break;
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(working, 0, 0, w, h);
    working = next;
    blob = await blobFromCanvas(working);
  }

  if (blob.size > MAX_ATTACHMENT_BYTES) {
    throw new Error("截屏文件过大，请缩小窗口或预览区域后重试。");
  }

  return new File([blob], filename, { type: "image/png" });
}
