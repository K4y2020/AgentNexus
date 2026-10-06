import { describe, expect, it } from "vitest";
import { reviewTime } from "./cineReview";

describe("reviewTime", () => {
  it("truncates to tenths like the film-analysis report", () => {
    expect(reviewTime(25)).toBe("00:25.0");
    expect(reviewTime(232.871)).toBe("03:52.8");
  });

  it("never shows sixty seconds", () => {
    expect(reviewTime(59.96)).toBe("00:59.9");
    expect(reviewTime(119.97)).toBe("01:59.9");
  });

  it("clamps negative times to zero", () => {
    expect(reviewTime(-1)).toBe("00:00.0");
  });
});
