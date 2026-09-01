/** Format cumulative session spend: `$x.xx`, or `<$0.01` for sub-cent. */
export function formatSessionCostUsd(costUsd: number): string {
  if (costUsd > 0 && costUsd < 0.01) {
    return "<$0.01";
  }
  return `$${costUsd.toFixed(2)}`;
}

/**
 * Compact token-count formatter, e.g. ``842`` -> ``"842"``,
 * ``12_400`` -> ``"12.4K"``, ``1_530_000`` -> ``"1.5M"``.
 * The locale is pinned to ``en-US`` so the K/M suffixes are stable across
 * hosts and browsers (the doc comment above is the contract, not whatever
 * number-format locale the viewer happens to run under).
 */
export function formatTokenCount(tokens: number): string {
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(tokens);
}
