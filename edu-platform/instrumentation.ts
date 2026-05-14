/**
 * Next.js instrumentation hook.
 * Keep this file runtime-agnostic and load Node-only code conditionally.
 */

export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./instrumentation-node");
  }
}
