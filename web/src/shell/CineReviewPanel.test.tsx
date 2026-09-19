import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { CineReviewPanel } from "./CineReviewPanel";
import { clearSessionDrafts, getSessionDraft, setSessionDraft } from "@/lib/sessionDrafts";
import { authenticatedFetch } from "@/lib/identity";

vi.mock("@/lib/identity", () => ({ authenticatedFetch: vi.fn() }));
vi.mock("@/lib/host", () => ({ getOmnigentHostConfig: () => ({}), getEmbedRoot: () => null }));
vi.mock("@/store/chatStore", () => ({
  useChatStore: (select: (s: unknown) => unknown) =>
    select({ status: "idle", conversationId: "a" }),
}));
vi.mock("@/components/SessionImage", () => ({ SessionImage: () => null }));

const report = {
  status: "ready",
  token: "v1",
  project: "projects/film",
  data: {
    sourceId: "source",
    revisionId: "revision",
    sourceName: "film.mp4",
    duration: 60,
    summary: { premise: "原片剧情" },
    characters: [],
    images: [],
    warnings: [],
    cues: [
      {
        id: "story:B001",
        sourceId: "B001",
        type: "story",
        start: 10,
        end: 20,
        title: "发现线索",
        text: "走进房间发现线索",
        status: "待核验",
        imageIds: [],
      },
    ],
  },
  productionPackages: [],
  production: null,
};
const production = {
  id: "work/production-3min",
  name: "production-3min",
  mode: "adaptation",
  scope: "完整原片时长版",
  targetSeconds: 232.871,
  stages: ["script", "storyboard", "mapping"],
  hashesMatch: true,
  mappingCount: 2,
  blockers: [],
  validation: {
    status: "native_validated",
    productionAuthorized: false,
    unverified: [],
    stageStatuses: {},
  },
  manifestToken: "manifest-1",
  artifacts: {
    source_material: { source_id: "source", revision_id: "revision" },
    cast: { characters: [{ id: "C01", name: "清涵" }] },
    art: { scenes: [{ id: "S01", name: "卧室" }] },
    script: {
      episodes: [
        {
          ep: 1,
          scenes: [
            {
              sceneId: "S01",
              flow: [
                { action: "她按住震动的手机。" },
                { speaker: "C01", line: "先别接。", delivery: "压低声音" },
                { action: "她走到门边。" },
              ],
            },
          ],
        },
      ],
    },
    storyboard: {
      episodes: [
        {
          ep: 1,
          segments: [
            {
              id: "E01-01",
              sceneIndex: 1,
              cuts: [
                {
                  seconds: 4,
                  beats: [1, 2],
                  frame: "A hand on a phone",
                  characters: ["C01"],
                  size: "close",
                },
                { seconds: 3, beats: [3, 3], frame: "At the door" },
                { seconds: 2, frame: "Unmapped closing shot" },
              ],
            },
          ],
        },
      ],
    },
    mapping: [
      { ep: 1, segment_id: "E01-01", cut: 1, kind: "adapted", source_shot_ids: ["S01", "S02"] },
      { ep: 1, segment_id: "E01-01", cut: 2, kind: "new", source_shot_ids: [] },
    ],
  },
};
const productionReport = {
  ...report,
  data: {
    ...report.data,
    cues: [
      ...report.data.cues,
      ...[
        { sourceId: "S01", start: 5, end: 9, title: "镜头 001", text: "原片手机" },
        { sourceId: "S02", start: 25, end: 30, title: "镜头 002", text: "原片门口" },
      ].map((cue) => ({
        ...cue,
        id: `shot:${cue.sourceId}`,
        type: "shot",
        status: "待核验",
        imageIds: [],
      })),
    ],
  },
  productionPackages: [production],
  production,
};
function mount(session = "a") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <CineReviewPanel conversationId={session} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  clearSessionDrafts();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.mocked(authenticatedFetch).mockResolvedValue(new Response(JSON.stringify(report)));
  // Each fetch needs its own body stream.
  vi.mocked(authenticatedFetch).mockImplementation(
    async () => new Response(JSON.stringify(report)),
  );
});
describe("Cine review", () => {
  it("seeks to selected cue and appends versioned feedback without sending or overwriting a draft", async () => {
    setSessionDraft("a", { text: "已有草稿", files: [] });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "剧情 00:10.0 发现线索" }));
    expect((screen.getByLabelText("原片播放器") as HTMLVideoElement).currentTime).toBe(10);
    fireEvent.change(screen.getByLabelText("复核意见"), { target: { value: "应是母亲发现" } });
    fireEvent.click(screen.getByRole("button", { name: "添加到当前聊天" }));
    await screen.findByText("已添加到聊天输入框，待发送。");
    expect(getSessionDraft("a")?.text).toContain("已有草稿");
    expect(getSessionDraft("a")?.text).toContain('"record_id": "story:B001"');
    expect(getSessionDraft("a")?.text).toContain('"revision_id": "revision"');
    expect(getSessionDraft("b")).toBeUndefined();
    expect(vi.mocked(authenticatedFetch).mock.calls.every(([, init]) => !init?.method)).toBe(true);
  });
  it("blocks feedback from an outdated snapshot", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "剧情 00:10.0 发现线索" }));
    fireEvent.change(screen.getByLabelText("复核意见"), { target: { value: "修正" } });
    vi.mocked(authenticatedFetch).mockImplementation(
      async () => new Response(JSON.stringify({ ...report, token: "v2" })),
    );
    fireEvent.click(screen.getByRole("button", { name: "添加到当前聊天" }));
    await screen.findByText("报告已有更新，请刷新后重新选择记录。");
    expect(getSessionDraft("a")).toBeUndefined();
  });
  it("shows the empty state for a different Topic", async () => {
    vi.mocked(authenticatedFetch).mockImplementation(
      async () => new Response(JSON.stringify({ status: "not_ready", message: "尚无报告" })),
    );
    mount("b");
    await waitFor(() => expect(screen.getByText("尚无报告")).toBeVisible());
    expect(screen.queryByLabelText("原片播放器")).toBeNull();
    expect(authenticatedFetch).toHaveBeenCalledWith(
      "/v1/sessions/b/cine-review",
      expect.anything(),
    );
  });
  it("shows adaptation beside the original without a second page or player", async () => {
    vi.mocked(authenticatedFetch).mockImplementation(
      async () => new Response(JSON.stringify(productionReport)),
    );
    const { container } = mount();
    await screen.findByText("她按住震动的手机。");
    expect(screen.queryByRole("tab", { name: "改编生产" })).toBeNull();
    expect(screen.getByRole("complementary", { name: "改编分镜视图" })).toBeVisible();
    expect(container.querySelectorAll("video")).toHaveLength(1);
    expect(screen.getByText("先别接。")).toBeVisible();
    expect(screen.getByText("Unmapped closing shot")).toBeVisible();
    expect(screen.getByText("新增镜头，无原片对应段落")).toBeVisible();
    const video = screen.getByLabelText("原片播放器") as HTMLVideoElement;
    fireEvent.click(screen.getByRole("button", { name: "原片 00:25.0 · 镜头 002" }));
    expect(video.currentTime).toBe(25);
    expect(screen.getByText("原片门口")).toBeVisible();
    fireEvent.change(screen.getByLabelText("复核意见"), { target: { value: "保留这段" } });
    fireEvent.click(screen.getByRole("switch", { name: "改编分镜" }));
    expect(screen.queryByRole("complementary", { name: "改编分镜视图" })).toBeNull();
    expect(screen.getByLabelText("原片播放器")).toBe(video);
    expect(video.currentTime).toBe(25);
    expect(screen.getByLabelText("复核意见")).toHaveValue("保留这段");
    fireEvent.click(screen.getByRole("switch", { name: "改编分镜" }));
    await screen.findByText("她按住震动的手机。");
    expect(screen.getByLabelText("原片播放器")).toBe(video);
    expect(authenticatedFetch).toHaveBeenCalledWith(
      "/v1/sessions/a/cine-review?production=work%2Fproduction-3min",
      expect.anything(),
    );
  });

  it("does not seek into a different source revision", async () => {
    vi.mocked(authenticatedFetch).mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            ...productionReport,
            production: {
              ...production,
              artifacts: {
                ...production.artifacts,
                source_material: { source_id: "source", revision_id: "old" },
              },
            },
          }),
        ),
    );
    mount();
    await screen.findByText("改编引用的原片或版本无法与当前报告核对，暂不联动定位。");
    expect(screen.queryByRole("button", { name: /原片 00:/ })).toBeNull();
    expect(screen.getByText("先别接。")).toBeVisible();
  });

  it("keeps the original usable when adaptation cannot load", async () => {
    vi.mocked(authenticatedFetch).mockImplementation(async (path) =>
      String(path).includes("?production=")
        ? new Response("unavailable", { status: 503 })
        : new Response(JSON.stringify({ ...productionReport, production: null })),
    );
    mount();
    await screen.findByText("报告暂时无法读取 (503)");
    fireEvent.click(screen.getByRole("button", { name: "剧情 00:10.0 发现线索" }));
    expect((screen.getByLabelText("原片播放器") as HTMLVideoElement).currentTime).toBe(10);
  });

  it("supports zooming the timeline tracks and reveals zoom levels", async () => {
    mount();
    expect(await screen.findByText("全片")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "放大时间线" }));
    expect(screen.getByText("1.5x")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "4x" }));
    expect(screen.getAllByText("4x")).toHaveLength(2);
    expect(screen.getByLabelText("跟随")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "适应" }));
    expect(screen.getByText("全片")).toBeVisible();
  });
});
