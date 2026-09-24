import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  MessageSquarePlusIcon,
  PlayIcon,
  RefreshCwIcon,
  RepeatIcon,
  ZoomInIcon,
  ZoomOutIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Switch } from "@/components/ui/switch";
import { CineAdaptationView } from "./CineAdaptationView";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { SessionImage } from "@/components/SessionImage";
import { ZoomableImage } from "@/components/ImageLightbox";
import { useChatStore } from "@/store/chatStore";
import { cn } from "@/lib/utils";
import { getOmnigentHostConfig } from "@/lib/host";
import {
  addCineReviewFeedback,
  fetchCineReview,
  reviewAsset,
  reviewFeedback,
  reviewKey,
  reviewTime,
  type CineReview,
  type ReviewCue,
} from "@/lib/cineReview";

export function CineReviewPanel({
  conversationId,
  onFeedback,
}: {
  conversationId: string;
  onFeedback?: () => void;
}) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: reviewKey(conversationId),
    queryFn: ({ signal }) => fetchCineReview(conversationId, signal),
    retry: false,
    staleTime: 0,
  });
  const status = useChatStore((s) => s.status);
  const activeSession = useChatStore((s) => s.conversationId);
  const previous = useRef(status);
  useEffect(() => {
    if (
      activeSession === conversationId &&
      previous.current === "streaming" &&
      status !== "streaming"
    )
      void queryClient.invalidateQueries({ queryKey: ["cine-review", conversationId] });
    previous.current = status;
  }, [status, activeSession, conversationId, queryClient]);
  const payload = query.data;
  const report: CineReview | null = payload && "data" in payload ? payload : null;
  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="原片复核">
      <div className="flex shrink-0 items-center gap-2 border-b px-3 py-2">
        <span className="min-w-0 flex-1 truncate text-ui" title={report?.data.sourceName}>
          {report?.data.sourceName ?? "原片复核"}
        </span>
        <Badge variant="secondary">待复核</Badge>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="刷新拉片报告"
              disabled={query.isFetching}
              onClick={() =>
                void queryClient.invalidateQueries({ queryKey: ["cine-review", conversationId] })
              }
            >
              <RefreshCwIcon className={query.isFetching ? "animate-spin" : undefined} />
            </Button>
          </TooltipTrigger>
          <TooltipContent>刷新拉片报告</TooltipContent>
        </Tooltip>
      </div>
      {query.isError && (
        <p role="alert" className="p-3 text-sm text-destructive">
          {query.error.message}
          {report ? "，仍显示上次载入的版本。" : ""}
        </p>
      )}
      {query.isPending && (
        <p role="status" className="p-3 text-sm text-muted-foreground">
          正在读取报告…
        </p>
      )}
      {query.data?.status === "not_ready" && (
        <p className="p-4 text-ui text-muted-foreground">{query.data.message}</p>
      )}
      {report && (
        <ReviewContent
          key={`${conversationId}:${report.token}`}
          session={conversationId}
          report={report}
          onFeedback={onFeedback}
          stale={query.isError}
        />
      )}
    </section>
  );
}

function ReviewContent({
  session,
  report,
  onFeedback,
  stale,
}: {
  session: string;
  report: CineReview;
  onFeedback?: () => void;
  stale: boolean;
}) {
  const { data, token } = report;
  const video = useRef<HTMLVideoElement>(null);
  const positionKey = `cine-review-position:${session}:${data.sourceId}:${data.revisionId}`;
  const [current, setCurrent] = useState(0);
  const [selected, setSelected] = useState<ReviewCue | null>(null);
  const [kind, setKind] = useState("story");
  const [search, setSearch] = useState("");
  const [note, setNote] = useState("");
  const [loop, setLoop] = useState(false);
  const [playingSelection, setPlayingSelection] = useState(false);
  const [mediaError, setMediaError] = useState("");
  const [feedbackAdded, setFeedbackAdded] = useState(false);
  const [addingFeedback, setAddingFeedback] = useState(false);
  const [showAdaptation, setShowAdaptation] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [follow, setFollow] = useState(true);
  const timelineScrollRef = useRef<HTMLDivElement>(null);
  const customFetcher = Boolean(getOmnigentHostConfig().fetcher);
  const selectedImages = data.images.filter((image) => selected?.imageIds.includes(image.id));
  const filtered = data.cues.filter(
    (cue) =>
      cue.type === kind &&
      `${cue.title} ${cue.text} ${cue.sourceId} ${cue.speaker ?? ""}`
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
  );
  const seek = (seconds: number) => {
    if (video.current) {
      video.current.currentTime = seconds;
      setCurrent(seconds);
    }
  };
  function select(cue: ReviewCue) {
    video.current?.pause();
    setSelected(cue);
    setNote("");
    setPlayingSelection(false);
    setFeedbackAdded(false);
    seek(cue.start);
  }
  function updateTime() {
    const player = video.current;
    if (!player) return;
    setCurrent(player.currentTime);
    try {
      sessionStorage.setItem(positionKey, String(player.currentTime));
    } catch {
      /* Storage optional. */
    }
    if (playingSelection && selected && player.currentTime >= selected.end) {
      if (loop) seek(selected.start);
      else {
        player.pause();
        setPlayingSelection(false);
        seek(selected.end);
      }
    }
  }
  async function feedback() {
    // Revalidate the snapshot before quoting it into the current conversation.
    if (addingFeedback) return;
    setAddingFeedback(true);
    try {
      const latest = await fetchCineReview(session);
      if (latest.status !== "ready" || latest.token !== token) {
        setMediaError("报告已有更新，请刷新后重新选择记录。");
        return;
      }
      if (!selected || !note.trim()) return;
      addCineReviewFeedback(session, reviewFeedback(report, selected, note));
      setNote("");
      setFeedbackAdded(true);
      onFeedback?.();
    } catch {
      setMediaError("暂时无法核对当前版本，请重试。");
    } finally {
      setAddingFeedback(false);
    }
  }

  useEffect(() => {
    if (!follow || zoom <= 1 || !timelineScrollRef.current || !data.duration) return;
    const scrollEl = timelineScrollRef.current;
    const playheadRatio = current / data.duration;
    const targetScrollLeft = playheadRatio * scrollEl.scrollWidth - scrollEl.clientWidth / 2;
    scrollEl.scrollLeft = Math.max(0, targetScrollLeft);
  }, [current, follow, zoom, data.duration]);

  const visibleSeconds = data.duration / zoom;
  const tickStep =
    [1, 2, 5, 10, 15, 30, 60, 120, 300, 600].find(
      (s) => (s / Math.max(1, visibleSeconds)) * 100 >= 10,
    ) || 30;
  const ticks: number[] = [];
  if (data.duration > 0) {
    for (let t = 0; t <= data.duration; t += tickStep) {
      ticks.push(t);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col text-ui">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b px-3 py-2">
        <span className="text-ui font-medium">原片</span>
        <label className="flex items-center gap-2 text-ui">
          改编分镜
          <Switch size="sm" checked={showAdaptation} onCheckedChange={setShowAdaptation} />
        </label>
      </div>
      <div
        className={cn("grid min-h-0 flex-1 overflow-auto", showAdaptation && "grid-cols-2")}
        data-cine-review-layout
      >
        <div className="flex min-h-0 min-w-0 flex-col p-3 text-ui" aria-label="原片内容">
          <div className="shrink-0">
            <div className="flex flex-wrap justify-between gap-2 pb-2 text-sm text-muted-foreground">
              <span>{report.project}</span>
              <span title={data.revisionId}>版本 {data.revisionId.slice(0, 12)}</span>
            </div>
            {customFetcher ? (
              <p role="alert">当前嵌入客户端暂不支持原片流播放，请使用独立网页。</p>
            ) : (
              <video
                ref={video}
                className="aspect-video w-full max-h-[30dvh] bg-muted object-contain"
                controls
                playsInline
                preload="metadata"
                aria-label="原片播放器"
                src={reviewAsset(session, token, "video")}
                onTimeUpdate={updateTime}
                onError={() => setMediaError("原片暂时无法播放，请检查文件或刷新报告。")}
                onLoadedMetadata={() => {
                  try {
                    const saved = Number(sessionStorage.getItem(positionKey));
                    if (Number.isFinite(saved) && saved >= 0 && saved < data.duration) seek(saved);
                  } catch {
                    /* Storage optional. */
                  }
                }}
              />
            )}
            {mediaError && (
              <p role="alert" className="py-2 text-sm text-destructive">
                {mediaError}
              </p>
            )}
            <div className="flex flex-wrap items-center justify-between gap-2 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm">
                  {reviewTime(current)} / {reviewTime(data.duration)}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!selected || customFetcher}
                  onClick={() => {
                    if (!selected) return;
                    seek(selected.start);
                    setPlayingSelection(true);
                    void video.current?.play().catch(() => setMediaError("播放失败，请检查原片。"));
                  }}
                >
                  <PlayIcon />
                  片段回放
                </Button>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant={loop ? "secondary" : "ghost"}
                      size="icon-sm"
                      aria-label="循环片段"
                      aria-pressed={loop}
                      onClick={() => setLoop(!loop)}
                    >
                      <RepeatIcon />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>循环片段</TooltipContent>
                </Tooltip>
              </div>

              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <span className="shrink-0 mr-0.5">缩放:</span>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      disabled={zoom <= 1}
                      onClick={() => setZoom((z) => Math.max(1, Math.round((z / 1.5) * 10) / 10))}
                      aria-label="缩小时间线"
                    >
                      <ZoomOutIcon className="size-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>缩小时间线</TooltipContent>
                </Tooltip>

                <span className="font-mono w-9 text-center text-foreground font-medium">
                  {zoom === 1 ? "全片" : `${zoom}x`}
                </span>

                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      disabled={zoom >= 16}
                      onClick={() => setZoom((z) => Math.min(16, Math.round(z * 1.5 * 10) / 10))}
                      aria-label="放大时间线"
                    >
                      <ZoomInIcon className="size-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>放大时间线</TooltipContent>
                </Tooltip>

                <div className="flex items-center rounded-sm bg-muted p-0.5 ml-1 gap-0.5">
                  {[1, 2, 4, 8].map((level) => (
                    <button
                      key={level}
                      type="button"
                      className={cn(
                        "px-1.5 py-0.5 rounded-xs text-[11px] font-medium transition-colors cursor-pointer",
                        zoom === level
                          ? "bg-background text-foreground shadow-xs"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                      onClick={() => setZoom(level)}
                    >
                      {level === 1 ? "适应" : `${level}x`}
                    </button>
                  ))}
                </div>

                {zoom > 1 && (
                  <label className="flex items-center gap-1 ml-1.5 cursor-pointer text-[11px] select-none">
                    <input
                      type="checkbox"
                      checked={follow}
                      onChange={(e) => setFollow(e.target.checked)}
                      className="rounded-xs size-3 cursor-pointer"
                    />
                    跟随
                  </label>
                )}
              </div>
            </div>

            <div className="flex border-y py-2 gap-2" aria-label="拉片时间线">
              <div className="w-8 shrink-0 flex flex-col justify-between py-0.5 text-xs text-muted-foreground font-medium select-none">
                <div className="h-4 flex items-center text-[10px]">刻度</div>
                <div className="h-8 flex items-center">剧情</div>
                <div className="h-8 flex items-center">镜头</div>
                <div className="h-8 flex items-center">对白</div>
              </div>

              <div
                ref={timelineScrollRef}
                className="relative flex-1 min-w-0 overflow-x-auto overflow-y-hidden rounded-sm bg-muted/40 p-1"
                tabIndex={0}
                aria-label="时间线轨道滚动区"
              >
                <div className="relative min-w-full space-y-1" style={{ width: `${zoom * 100}%` }}>
                  <div
                    className="absolute top-0 bottom-0 w-[2px] bg-red-500 z-20 pointer-events-none transition-[left] duration-75"
                    style={{ left: `${(current / Math.max(1, data.duration)) * 100}%` }}
                  >
                    <div className="w-2.5 h-2.5 -ml-1 bg-red-500 rotate-45 -mt-1 rounded-[1px]" />
                  </div>

                  <div
                    className="relative h-4 w-full cursor-pointer text-[10px] text-muted-foreground border-b border-border/50 select-none overflow-hidden"
                    onClick={(e) => {
                      const rect = e.currentTarget.getBoundingClientRect();
                      const ratio = (e.clientX - rect.left) / rect.width;
                      seek(Math.max(0, Math.min(data.duration, ratio * data.duration)));
                    }}
                  >
                    {ticks.map((t) => (
                      <span
                        key={t}
                        className="absolute -translate-x-1/2 font-mono whitespace-nowrap"
                        style={{ left: `${(t / Math.max(1, data.duration)) * 100}%` }}
                      >
                        {reviewTime(t)}
                      </span>
                    ))}
                  </div>

                  {(
                    [
                      ["story", "剧情"],
                      ["shot", "镜头"],
                      ["dialogue", "对白"],
                    ] as const
                  ).map(([type, label]) => (
                    <div
                      key={type}
                      className="relative h-8 w-full overflow-hidden rounded-sm bg-muted cursor-pointer"
                      onClick={(e) => {
                        const rect = e.currentTarget.getBoundingClientRect();
                        const ratio = (e.clientX - rect.left) / rect.width;
                        seek(Math.max(0, Math.min(data.duration, ratio * data.duration)));
                      }}
                    >
                      {data.cues
                        .filter((cue) => cue.type === type)
                        .map((cue) => (
                          <button
                            key={cue.id}
                            type="button"
                            aria-label={`${label} ${reviewTime(cue.start)} ${cue.title}`}
                            title={`${label} · ${reviewTime(cue.start)}–${reviewTime(cue.end)} · ${cue.title}`}
                            className={cn(
                              "absolute inset-y-0.5 min-w-[2px] overflow-hidden border-l border-background/60 px-1 text-left flex items-center cursor-pointer transition-colors focus:z-10 focus:outline focus:outline-2",
                              selected?.id === cue.id
                                ? "bg-primary text-primary-foreground font-semibold shadow-sm z-[2]"
                                : current >= cue.start && current < cue.end
                                  ? "bg-foreground/60 text-background font-medium"
                                  : "bg-foreground/20 text-foreground/85 hover:bg-foreground/30 hover:text-foreground",
                            )}
                            style={{
                              left: `${(cue.start / Math.max(1, data.duration)) * 100}%`,
                              width: `${Math.max(((cue.end - cue.start) / Math.max(1, data.duration)) * 100, 0.4)}%`,
                            }}
                            onClick={(e) => {
                              e.stopPropagation();
                              setKind(type);
                              select(cue);
                            }}
                          >
                            <span className="truncate block w-full text-[11px] leading-tight select-none pointer-events-none">
                              {type === "story" &&
                              cue.sourceId &&
                              !cue.title.startsWith(cue.sourceId)
                                ? `${cue.sourceId} ${cue.title}`
                                : cue.title}
                            </span>
                          </button>
                        ))}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto" data-review-records>
            <details className="border-b py-3" open={!selected}>
              <summary className="cursor-pointer font-medium">剧情概览</summary>
              {(
                [
                  ["premise", "开端"],
                  ["conflict", "冲突"],
                  ["turning_points", "转折"],
                  ["ending", "结尾"],
                ] as const
              ).map(([key, label]) => (
                <p key={key} className="mt-2 whitespace-pre-wrap break-words">
                  <span className="text-muted-foreground">{label}：</span>
                  {Array.isArray(data.summary[key])
                    ? data.summary[key].join("\n")
                    : data.summary[key] || "尚未记录"}
                </p>
              ))}
              {Array.from(new Set(data.characters)).map((person) => (
                <p key={person} className="mt-2 break-words">
                  {person}
                </p>
              ))}
            </details>
            {selected && (
              <section className="space-y-2 border-b py-3" aria-label="选中记录">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">{selected.status}</Badge>
                  <span className="font-mono text-sm">
                    {reviewTime(selected.start)} - {reviewTime(selected.end)}
                  </span>
                </div>
                <p className="whitespace-pre-wrap break-words">{selected.text || "尚无逐镜描述"}</p>
                {(selected.subtitleText || selected.asrText) && (
                  <div className="space-y-1 rounded-md bg-muted p-2 text-sm">
                    <p className="text-muted-foreground">
                      对白来源：{selected.provenance || "未声明"}
                    </p>
                    {selected.subtitleText && <p>字幕：{selected.subtitleText}</p>}
                    {selected.asrText && <p>ASR：{selected.asrText}</p>}
                    {selected.conflicts?.map((conflict) => (
                      <p key={conflict} className="text-destructive">
                        {conflict}
                      </p>
                    ))}
                  </div>
                )}
                {selected.speaker && <p className="text-muted-foreground">{selected.speaker}</p>}
                {selected.connection && <p className="break-words">承接：{selected.connection}</p>}
                {selected.timingNote && (
                  <p className="text-sm text-muted-foreground">{selected.timingNote}</p>
                )}
                {Array.from(new Set(selected.uncertainties)).map((text) => (
                  <p key={text} className="text-sm text-muted-foreground">
                    {text}
                  </p>
                ))}
                <div className="flex gap-2 overflow-x-auto">
                  {selectedImages.map((image) => (
                    <div key={image.id} className="w-40 shrink-0">
                      {customFetcher ? (
                        <SessionImage
                          path={reviewAsset(session, token, image.id)}
                          alt={`原片抽帧 ${reviewTime(image.start)}`}
                        />
                      ) : (
                        <ZoomableImage
                          src={reviewAsset(session, token, image.id)}
                          alt={`原片抽帧 ${reviewTime(image.start)}`}
                          className="aspect-video w-40 object-contain"
                          loading="lazy"
                        />
                      )}
                      <p className="text-sm text-muted-foreground">{reviewTime(image.start)}</p>
                    </div>
                  ))}
                </div>
                <label htmlFor="cine-review-note" className="text-sm">
                  复核意见
                </label>
                <Textarea
                  id="cine-review-note"
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  rows={2}
                />
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!note.trim() || stale || addingFeedback}
                  onClick={() => void feedback()}
                >
                  <MessageSquarePlusIcon />
                  添加到当前聊天
                </Button>
                {feedbackAdded && (
                  <p role="status" className="text-sm text-muted-foreground">
                    已添加到聊天输入框，待发送。
                  </p>
                )}
              </section>
            )}
            <Tabs value={kind} onValueChange={setKind} className="pt-3">
              <TabsList
                variant="line"
                className="max-w-full flex-wrap group-data-horizontal/tabs:h-auto"
              >
                {(
                  [
                    ["story", "剧情"],
                    ["shot", "镜头"],
                    ["dialogue", "对白"],
                  ] as const
                ).map(([type, label]) => (
                  <TabsTrigger key={type} value={type}>
                    {label} {data.cues.filter((cue) => cue.type === type).length}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <Input
              aria-label="搜索拉片记录"
              placeholder="搜索记录"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="my-2"
            />
            <div className="divide-y">
              {filtered.map((cue) => (
                <button
                  type="button"
                  key={cue.id}
                  onClick={() => select(cue)}
                  aria-pressed={selected?.id === cue.id}
                  className={cn(
                    "flex w-full gap-3 px-2 py-3 text-left hover:bg-muted focus-visible:outline focus-visible:outline-2",
                    selected?.id === cue.id && "bg-muted",
                  )}
                >
                  <span className="shrink-0 font-mono text-sm text-muted-foreground">
                    {reviewTime(cue.start)}
                  </span>
                  <span className="min-w-0 break-words">{cue.title}</span>
                </button>
              ))}
            </div>
            {!filtered.length && <p className="py-3 text-muted-foreground">暂无匹配记录</p>}
            {data.warnings.map((warning) => (
              <p className="mt-2 text-sm text-muted-foreground" key={warning}>
                {warning}
              </p>
            ))}
          </div>
        </div>
        {showAdaptation && (
          <CineAdaptationView
            key={session}
            session={session}
            report={report}
            current={current}
            onSelectSource={select}
          />
        )}
      </div>
    </div>
  );
}
