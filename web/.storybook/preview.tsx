import type { Preview } from "@storybook/react-vite";
import { addons } from "storybook/preview-api";
import {
  PLAY_FUNCTION_THREW_EXCEPTION,
  STORY_FINISHED,
  STORY_PREPARED,
  type StoryFinishedPayload,
} from "storybook/internal/core-events";
import { ThemeProvider } from "next-themes";
import { TooltipProvider } from "@/components/ui/tooltip";
import "katex/dist/katex.min.css";
import "streamdown/styles.css";
import "../src/index.css";

const storybookChannel = addons.getChannel();
storybookChannel.on(STORY_PREPARED, () => {
  delete document.documentElement.dataset.storybookPlayError;
  delete document.documentElement.dataset.storybookStoryId;
  delete document.documentElement.dataset.storybookStoryStatus;
});
storybookChannel.on(PLAY_FUNCTION_THREW_EXCEPTION, () => {
  document.documentElement.dataset.storybookPlayError = "true";
});
storybookChannel.on(STORY_FINISHED, ({ storyId, status }: StoryFinishedPayload) => {
  document.documentElement.dataset.storybookStoryId = storyId;
  document.documentElement.dataset.storybookStoryStatus = status;
});

const preview: Preview = {
  initialGlobals: { theme: "light" },
  globalTypes: {
    theme: {
      description: "UI color mode",
      toolbar: { icon: "circlehollow", items: ["light", "dark"], dynamicTitle: true },
    },
  },
  decorators: [
    (Story, context) => (
      <ThemeProvider attribute="class" forcedTheme={context.globals.theme} enableSystem={false}>
        <TooltipProvider>
          <div className="w-full min-w-0 max-w-3xl bg-background p-4 text-foreground sm:p-6">
            <Story />
          </div>
        </TooltipProvider>
      </ThemeProvider>
    ),
  ],
  parameters: {
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },
    layout: "padded",
  },
};

export default preview;
