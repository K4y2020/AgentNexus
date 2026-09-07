import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { onSeedanceCanvasOpen, openSeedanceCanvas, seedanceEmbedUrl } from "./seedanceCanvas";

describe("Seedance canvas routing", () => {
  it("preserves the project and enables embedding without accepting executable URLs", () => {
    expect(seedanceEmbedUrl("http://127.0.0.1:5173/?project=topic-a")).toBe(
      "http://127.0.0.1:5173/?project=topic-a&embed=1",
    );
    expect(seedanceEmbedUrl("javascript:alert(1)")).toBeNull();
    expect(seedanceEmbedUrl("http://secret@localhost/")).toBeNull();
  });

  it("routes a normal click to the panel and retains explicit new-window gestures", () => {
    const listener = vi.fn();
    const unsubscribe = onSeedanceCanvasOpen(listener);
    render(
      <a href="http://127.0.0.1:5173/?project=topic-b" onClick={openSeedanceCanvas}>
        Canvas
      </a>,
    );
    expect(fireEvent.click(screen.getByText("Canvas"))).toBe(false);
    expect(listener).toHaveBeenCalledWith("http://127.0.0.1:5173/?project=topic-b&embed=1");
    listener.mockClear();
    fireEvent.click(screen.getByText("Canvas"), { ctrlKey: true });
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });
});
