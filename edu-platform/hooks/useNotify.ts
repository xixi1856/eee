"use client";

import { useState } from "react";

export type Notification = { type: "success" | "error"; msg: string } | null;

export function useNotify() {
  const [notification, setNotification] = useState<Notification>(null);

  function notify(type: "success" | "error", msg: string) {
    setNotification({ type, msg });
    setTimeout(() => setNotification(null), 4000);
  }

  return { notification, notify };
}
