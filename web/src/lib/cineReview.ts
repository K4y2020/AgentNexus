import { z } from "zod";
import { authenticatedFetch } from "./identity";
import { getSessionDraft, setSessionDraft } from "./sessionDrafts";

const interval = {
  start: z.number().finite().nonnegative(),
  end: z.number().finite().nonnegative(),
};
const cueSchema = z.object({
  id: z.string(),
  sourceId: z.string(),
  type: z.enum(["story", "shot", "dialogue"]),
  ...interval,
  title: z.string(),
  text: z.string(),
  status: z.string(),
  imageIds: z.array(z.string()),
  speaker: z.string().optional(),
  connection: z.string().optional(),
  uncertainties: z.array(z.string()).optional(),
  timingNote: z.string().optional(),
  subtitleText: z.string().nullable().optional(),
  asrText: z.string().nullable().optional(),
  provenance: z.string().optional(),
  conflicts: z.array(z.string()).optional(),
});
const productionSummarySchema = z.object({
  id: z.string(),
  name: z.string(),
  mode: z.string().nullable().optional(),
  scope: z.string().nullable().optional(),
  targetSeconds: z.number().nullable().optional(),
  stages: z.array(z.string()),
  hashesMatch: z.boolean(),
  mappingCount: z.number().int().nonnegative(),
  blockers: z.array(z.string()),
  validation: z.object({
    status: z.string(),
    runId: z.string().nullable().optional(),
    productionAuthorized: z.boolean(),
    unverified: z.array(z.string()),
    stageStatuses: z.record(z.string(), z.string()),
  }),
});
const productionSchema = productionSummarySchema.extend({
  manifestToken: z.string(),
  artifacts: z.record(z.string(), z.unknown()),
});
const readySchema = z.object({
  status: z.literal("ready"),
  token: z.string(),
  project: z.string(),
  data: z.object({
    sourceId: z.string(),
    revisionId: z.string(),
    sourceName: z.string(),
    duration: z.number().finite().nonnegative(),
    cues: z.array(cueSchema),
    images: z.array(z.object({ id: z.string(), ...interval, kind: z.string() })),
    summary: z.record(z.string(), z.union([z.string(), z.array(z.string())])),
    characters: z.array(z.string()),
    warnings: z.array(z.string()),
  }),
  productionPackages: z.array(productionSummarySchema),
  production: productionSchema.nullable(),
});
const responseSchema = z.discriminatedUnion("status", [
  readySchema,
  z.object({ status: z.literal("not_ready"), message: z.string() }),
]);
export type CineReview = z.infer<typeof readySchema>;
export type ReviewCue = z.infer<typeof cueSchema>;
export const reviewKey = (session: string, production?: string) =>
  ["cine-review", session, production ?? null] as const;
export const reviewPath = (session: string) =>
  `/v1/sessions/${encodeURIComponent(session)}/cine-review`;
export const reviewAsset = (session: string, token: string, asset: string) =>
  `${reviewPath(session)}/assets/${encodeURIComponent(asset)}?token=${encodeURIComponent(token)}`;

export async function fetchCineReview(session: string, signal?: AbortSignal, production?: string) {
  const suffix = production ? `?production=${encodeURIComponent(production)}` : "";
  const response = await authenticatedFetch(`${reviewPath(session)}${suffix}`, { signal });
  if (!response.ok) throw new Error(`报告暂时无法读取 (${response.status})`);
  return responseSchema.parse(await response.json());
}

const openListeners = new Set<(session: string) => void>();
export function openCineReview(session: string) {
  openListeners.forEach((fn) => fn(session));
}
export function onCineReviewOpen(fn: (session: string) => void) {
  openListeners.add(fn);
  return () => {
    openListeners.delete(fn);
  };
}
const feedbackListeners = new Set<(session: string, text: string) => void>();
export function onCineReviewFeedback(fn: (session: string, text: string) => void) {
  feedbackListeners.add(fn);
  return () => {
    feedbackListeners.delete(fn);
  };
}
export function addCineReviewFeedback(session: string, text: string) {
  const draft = getSessionDraft(session);
  setSessionDraft(session, {
    text: [draft?.text, text].filter(Boolean).join("\n\n"),
    files: draft?.files ?? [],
  });
  feedbackListeners.forEach((fn) => fn(session, text));
}
export function reviewFeedback(report: CineReview, cue: ReviewCue, note: string): string {
  return (
    `请复核并修正当前 Topic 的原片分析，完成后刷新拉片报告。先核对当前项目与版本；若不匹配，请说明冲突。\n\n` +
    JSON.stringify(
      {
        kind: "cine_source_review_feedback",
        project: report.project,
        source_id: report.data.sourceId,
        revision_id: report.data.revisionId,
        report_token: report.token,
        record_id: cue.id,
        source_record_id: cue.sourceId,
        record_type: cue.type,
        interval_seconds: [cue.start, cue.end],
        original_text: cue.text,
        evidence_ids: cue.imageIds,
        user_feedback: note.trim(),
      },
      null,
      2,
    )
  );
}
export function reviewTime(value: number) {
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${(value % 60).toFixed(1).padStart(4, "0")}`;
}
