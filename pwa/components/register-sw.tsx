"use client";

import { useEffect } from "react";

/** Registers /sw.js on mount (production only, when service workers exist). */
export function RegisterSW() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Service worker registration is best-effort; the app works without it.
    });
  }, []);

  return null;
}
