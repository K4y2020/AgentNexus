import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { SeedanceCanvasPanel } from "./SeedanceCanvasPanel";

describe("Seedance panel views", () => {
  it("embeds one native workbench with its bound session instead of separate view tabs", () => {
    render(
      <TooltipProvider>
        <SeedanceCanvasPanel url="http://127.0.0.1:5173/?project=p1&session=s1&embed=1" />
      </TooltipProvider>,
    );
    const canvas = screen.getByTitle("Seedance V3 canvas");
    expect(screen.queryByTitle("Seedance V3 Agent")).toBeNull();
    expect(screen.queryByRole("tab", { name: "V3 Agent" })).toBeNull();
    expect(canvas).toHaveAttribute(
      "src",
      "http://127.0.0.1:5173/?project=p1&session=s1&embed=1",
    );
    fireEvent.click(screen.getByRole("button", { name: "Reload canvas" }));
    expect(screen.getByTitle("Seedance V3 canvas")).not.toBe(canvas);
    expect(screen.getByTitle("Seedance V3 canvas")).toHaveAttribute("src", "http://127.0.0.1:5173/?project=p1&session=s1&embed=1");
  });
});
