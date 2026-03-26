import { useLayoutEffect, useRef } from "react";

export function useAutosizeTextarea<T extends HTMLTextAreaElement>(value: string) {
  const ref = useRef<T | null>(null);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) {
      return;
    }
    element.style.height = "0px";
    element.style.height = `${Math.min(element.scrollHeight, 240)}px`;
  }, [value]);

  return ref;
}
