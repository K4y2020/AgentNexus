import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { renderTextWithInlineCode } from "./CliCommandBlock";

describe("renderTextWithInlineCode", () => {
  it("renders backtick spans as copyable code and leaves plain text selectable", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    render(
      <div>
        {renderTextWithInlineCode(
          "Run `agentnexus login https://x` and retry, or run `agentnexus setup`.",
        )}
      </div>,
    );

    // Both commands render as <code>.
    expect(screen.getByText("agentnexus login https://x").tagName).toBe("CODE");
    expect(screen.getByText("agentnexus setup").tagName).toBe("CODE");
    // Plain text between them is preserved.
    expect(screen.getByText(/and retry, or run/)).toBeInTheDocument();

    // Copy button copies just its command.
    fireEvent.click(screen.getAllByRole("button", { name: "Copy command" })[1]!);
    expect(writeText).toHaveBeenCalledWith("agentnexus setup");

    vi.unstubAllGlobals();
  });
});
