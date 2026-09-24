import type { MouseEvent } from "react";
import { createContext } from "react";
import { agentRootName } from "./forkHarness";

export const SeedanceCanvasContext = createContext(false);
export function isCineAgent(name: string | null | undefined): boolean {
  return (
    agentRootName(name ?? "")
      .trim()
      .toLowerCase() === "cine"
  );
}

const listeners = new Set<(url: string) => void>();

export function seedanceEmbedUrl(value: string): string | null {
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
    url.searchParams.set("embed", "1");
    return url.href;
  } catch {
    return null;
  }
}

export function openSeedanceCanvas(event: MouseEvent<HTMLAnchorElement>) {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)
    return;
  const url = seedanceEmbedUrl(event.currentTarget.href);
  if (!url || listeners.size === 0) return;
  event.preventDefault();
  for (const listener of listeners) listener(url);
}

export function onSeedanceCanvasOpen(listener: (url: string) => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
