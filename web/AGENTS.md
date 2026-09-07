# Frontend UI conventions

Read `STYLE_GUIDE.md` before adding or changing frontend UI.

- Use the existing shadcn/Radix components in `src/components/ui`; do not
  recreate buttons, inputs, selects, tabs, dialogs, badges, or tooltips in pages.
- Use component `variant` and `size` props. Add a shared variant when a real
  repeated need is missing instead of overriding its appearance at each caller.
- Use semantic theme tokens for color and `text-ui` / `text-sm` for interface
  typography. Do not add literal colors, palette utility colors, pixel font
  sizes, or negative letter spacing to product components.
- Tailwind layout utilities are allowed. Inline styles are reserved for
  measured geometry, user-provided values, or third-party rendering APIs.
- Use Lucide icons and accessible names for icon buttons. Status must include
  text, not color alone. Never invent a status when backend state is unknown.
- Maintain `Foundations/Primitives` in Storybook when changing shared variants.
  Verify light/dark themes, narrow viewports, and keyboard focus.
- Migrate legacy page styling when touching that surface; avoid unrelated bulk
  rewrites. Existing specialized editors and visualization renderers are not
  ordinary form controls.
