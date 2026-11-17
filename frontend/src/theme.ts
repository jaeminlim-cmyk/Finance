import { useSyncExternalStore } from "react";

type Theme = "dark" | "light";
const subs = new Set<() => void>();

function read(): Theme {
  return (document.documentElement.dataset.theme as Theme) || "dark";
}

export function initTheme() {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem("theme");
  } catch {
    /* 저장소를 못 쓰면 기본값 사용 */
  }
  document.documentElement.dataset.theme = saved === "light" || saved === "dark" ? saved : "dark";
}

export function toggleTheme() {
  const next: Theme = read() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("theme", next);
  } catch {
    /* 무시 */
  }
  subs.forEach((fn) => fn());
}

/** 테마가 바뀌면 차트가 색 토큰을 다시 읽도록 구독한다. */
export function useTheme(): Theme {
  return useSyncExternalStore((fn) => (subs.add(fn), () => subs.delete(fn)), read);
}
