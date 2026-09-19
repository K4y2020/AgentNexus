import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDownIcon, CrosshairIcon, FilmIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  fetchCineReview,
  reviewAsset,
  reviewKey,
  reviewTime,
  type CineReview,
  type ReviewCue,
} from "@/lib/cineReview";

type JsonRecord = Record<string, unknown>;
function record(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as JsonRecord) : {};
}
function rows(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.map(record) : [];
}
function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}
function oneBased(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : null;
}
const sizes: Record<string, string> = {
  "extreme-close": "大特写",
  close: "特写",
  "medium-close": "近景",
  medium: "中景",
  "medium-wide": "中全景",
  wide: "全景",
  "extreme-wide": "远景",
};
const kinds: Record<string, string> = {
  retained: "保留",
  adapted: "改编",
  merged: "合并",
  new: "新增",
};

export function CineAdaptationView({
  session,
  report,
  current,
  onSelectSource,
}: {
  session: string;
  report: CineReview;
  current: number;
  onSelectSource: (cue: ReviewCue) => void;
}) {
  const [selectedPackage, setSelectedPackage] = useState("");
  const packages = report.productionPackages;
  const productionId = packages.find((item) => item.id === selectedPackage)?.id ?? packages[0]?.id;
  const query = useQuery({
    queryKey: reviewKey(session, productionId),
    queryFn: ({ signal }) => fetchCineReview(session, signal, productionId),
    enabled: Boolean(productionId),
    retry: false,
  });
  const payload = query.data;
  const snapshot: CineReview | null = payload && "data" in payload ? payload : null;
  const production = snapshot?.production;
  const sourceMaterial = record(production?.artifacts.source_material);
  // Never pair different source revisions just because their shot IDs happen to match.
  const sourceMatches =
    snapshot?.token === report.token &&
    sourceMaterial.source_id === report.data.sourceId &&
    sourceMaterial.revision_id === report.data.revisionId;
  const [selection, setSelection] = useState<{ packageId: string; key: string } | null>(null);
  const shots = useMemo(() => {
    const sourceCues = new Map(
      report.data.cues.filter((cue) => cue.type === "shot").map((cue) => [cue.sourceId, cue]),
    );
    const mappings = rows(production?.artifacts.mapping);
    const scriptEpisodes = rows(record(production?.artifacts.script).episodes);
    const sceneNames = new Map(
      rows(record(production?.artifacts.art).scenes).map((scene) => [
        text(scene.id),
        text(scene.name),
      ]),
    );
    const characters = rows(record(production?.artifacts.cast).characters);
    const names = new Map(characters.map((person) => [text(person.id), text(person.name)]));
    return rows(record(production?.artifacts.storyboard).episodes).flatMap((episode, epIndex) => {
      const ep = oneBased(episode.ep) ?? epIndex + 1;
      const script = scriptEpisodes.find((item) => item.ep === ep) ?? scriptEpisodes[epIndex];
      let offset = 0;
      return rows(episode.segments).flatMap((segment, segmentIndex) => {
        const segmentId = text(segment.id);
        const sceneIndex = oneBased(segment.sceneIndex);
        const scene = sceneIndex ? rows(script?.scenes)[sceneIndex - 1] : undefined;
        return rows(segment.cuts).map((cut, cutIndex) => {
          const key = `${ep}:${segmentId || segmentIndex}:${cutIndex + 1}`;
          const mappingRows = mappings.filter(
            (row) => row.segment_id === segmentId && row.cut === cutIndex + 1 && row.ep === ep,
          );
          const sourceIds = [
            ...new Set(
              mappingRows.flatMap((row) =>
                Array.isArray(row.source_shot_ids)
                  ? row.source_shot_ids.filter((id): id is string => typeof id === "string")
                  : [],
              ),
            ),
          ];
          const sources = sourceMatches
            ? sourceIds.flatMap((id) => (sourceCues.has(id) ? [sourceCues.get(id)!] : []))
            : [];
          const beats = Array.isArray(cut.beats) ? cut.beats : [];
          const from = oneBased(beats[0]);
          const to = oneBased(beats[1]);
          const flow = rows(scene?.flow);
          const lines =
            from && to && to >= from && to <= flow.length ? flow.slice(from - 1, to) : [];
          const duration =
            typeof cut.seconds === "number" && Number.isFinite(cut.seconds) && cut.seconds > 0
              ? cut.seconds
              : 0;
          const start = offset;
          offset += duration;
          const cast = Array.isArray(cut.characters)
            ? cut.characters.filter((id): id is string => typeof id === "string")
            : [];
          const states = record(cut.characterStates);
          return {
            key,
            ep,
            id: segmentId,
            cutNumber: cutIndex + 1,
            start,
            end: offset,
            duration,
            scene: sceneNames.get(text(scene?.sceneId)) || text(scene?.sceneId),
            frame: text(cut.frame),
            size: sizes[text(cut.size)] || text(cut.size),
            camera: text(cut.camera),
            kind:
              [...new Set(mappingRows.map((row) => kinds[text(row.kind)] || "关系未标注"))].join(
                " / ",
              ) || "未关联",
            sources,
            missingSources: sourceIds.length - sources.length,
            active: sources.some((cue) => current >= cue.start && current < cue.end),
            lines: lines.map((line, index) => ({
              beat: (from ?? 1) + index,
              action: text(line.action),
              line: text(line.line),
              delivery: text(line.delivery),
              speaker:
                names.get(text(line.speaker)) ||
                (line.speaker === "VO"
                  ? "画外音（人物未标明）"
                  : text(line.speaker) || "说话人未标明"),
            })),
            characters: cast.map((id) => {
              const state = rows(characters.find((person) => person.id === id)?.states).find(
                (candidate) => candidate.id === states[id],
              );
              return `${names.get(id) || id}${state ? ` · ${text(state.label) || text(state.id)}` : ""}`;
            }),
          };
        });
      });
    });
  }, [production, report.data.cues, sourceMatches, current]);
  const selectedKey = selection && selection.packageId === productionId ? selection.key : null;

  return (
    <aside className="flex min-h-0 min-w-0 flex-col border-l" aria-label="改编分镜视图">
      <div className="shrink-0 space-y-2 border-b p-3">
        <div className="flex items-center gap-2 text-ui font-medium">
          <FilmIcon className="size-4" />
          改编分镜{" "}
          <span className="text-sm text-muted-foreground">
            {shots.length ? `${shots.length} 镜` : ""}
          </span>
        </div>
        {packages.length > 0 && (
          <Select value={productionId} onValueChange={setSelectedPackage}>
            <SelectTrigger size="sm" className="w-full min-w-0" aria-label="改编版本">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {packages.map((item, index) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.targetSeconds != null ? `${reviewTime(item.targetSeconds)} · ` : ""}
                  版本 {index + 1}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3 text-ui">
        {!packages.length && (
          <p className="text-sm text-muted-foreground">当前原片还没有改编分镜。</p>
        )}
        {productionId && query.isPending && <p role="status">正在读取改编分镜…</p>}
        {query.isError && (
          <p role="alert" className="text-destructive">
            {query.error.message}
          </p>
        )}
        {query.data?.status === "not_ready" && <p>{query.data.message}</p>}
        {production && !sourceMatches && (
          <p role="alert" className="text-sm text-warning">
            改编引用的原片或版本无法与当前报告核对，暂不联动定位。
          </p>
        )}
        {production && !shots.length && <p>当前版本尚无分镜内容。</p>}
        {shots.map((shot) => (
          <article
            key={shot.key}
            className={cn(
              "space-y-2 border-l-2 py-2 pl-3",
              shot.active || selectedKey === shot.key ? "border-primary bg-muted" : "border-border",
            )}
            aria-label={`第 ${shot.ep} 集 ${shot.id} 镜头 ${shot.cutNumber}`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium">
                {shot.id} · 镜头 {shot.cutNumber}
              </span>
              <Badge variant="secondary">{shot.kind}</Badge>
            </div>
            <p className="text-sm text-muted-foreground">
              {shot.scene} · {reviewTime(shot.start)}–{reviewTime(shot.end)} ·{" "}
              {shot.duration ? `${shot.duration} 秒` : "时长未记录"}
            </p>
            <p className="text-sm">
              {[shot.size, shot.camera].filter(Boolean).join(" · ") || "镜头参数未记录"}
            </p>
            {shot.characters.length > 0 && (
              <p className="text-sm text-muted-foreground">{shot.characters.join("、")}</p>
            )}
            {shot.lines.map((line) => (
              <div
                key={`${shot.key}:beat:${line.beat}`}
                className="space-y-1 whitespace-pre-wrap break-words"
              >
                {line.action && <p>{line.action}</p>}
                {line.line && (
                  <p>
                    <strong>{line.speaker}：</strong>
                    {line.line}
                  </p>
                )}
                {line.delivery && <p className="text-sm text-muted-foreground">{line.delivery}</p>}
              </div>
            ))}
            {!shot.lines.length && (
              <p className="whitespace-pre-wrap break-words">{shot.frame || "画面描述未记录"}</p>
            )}
            {shot.lines.length > 0 && shot.frame && (
              <Collapsible>
                <CollapsibleTrigger asChild>
                  <Button variant="ghost" size="sm">
                    <ChevronDownIcon />
                    画面提示词
                  </Button>
                </CollapsibleTrigger>
                <CollapsibleContent className="whitespace-pre-wrap break-words py-2 text-sm">
                  {shot.frame}
                </CollapsibleContent>
              </Collapsible>
            )}
            <div className="flex flex-col gap-2">
              {shot.sources.map((cue) => {
                const cueImage = report.data.images.find((img) =>
                  cue.imageIds.includes(img.id),
                );
                return (
                  <div
                    key={cue.id}
                    className="flex items-center gap-2 rounded-md border bg-card/60 p-1.5 text-xs shadow-2xs"
                  >
                    {cueImage && (
                      <img
                        src={reviewAsset(session, report.token, cueImage.id)}
                        alt={`原片截图 ${reviewTime(cue.start)}`}
                        className="h-12 w-20 shrink-0 rounded object-cover border bg-muted"
                      />
                    )}
                    <Button
                      size="list"
                      variant="outline"
                      className="min-w-0 flex-1 justify-start gap-1.5 text-left"
                      onClick={() => {
                        setSelection({ packageId: productionId!, key: shot.key });
                        onSelectSource(cue);
                      }}
                    >
                      <CrosshairIcon className="size-3.5 shrink-0" />
                      <span className="truncate">
                        原片 {reviewTime(cue.start)} · {cue.title}
                      </span>
                    </Button>
                  </div>
                );
              })}
            </div>
            {(shot.missingSources > 0 || !shot.sources.length) && (
              <p className="text-sm text-muted-foreground">
                {shot.kind === "新增" && !shot.missingSources
                  ? "新增镜头，无原片对应段落"
                  : "原片对应关系待核对"}
              </p>
            )}
          </article>
        ))}
      </div>
    </aside>
  );
}
