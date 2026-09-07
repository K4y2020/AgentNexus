import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter } from "react-router-dom";
import { A2ACollaborationCard } from "./A2ACollaborationCard";

const meta = {
  title: "Components/Blocks/A2ACollaborationCard",
  component: A2ACollaborationCard,
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
  args: {
    arguments: {
      teammate: "polly",
      task: "Review the model routing and return the findings to this conversation.\nCheck the configured model and the effective model.",
      intent: "review.request",
      file_ids: ["routing-report.md"],
    },
    output: null,
    state: "input-available",
  },
} satisfies Meta<typeof A2ACollaborationCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Running: Story = {};
export const Completed: Story = {
  args: {
    state: "output-available",
    duration: 19.2,
    output: JSON.stringify({
      status: "completed",
      target_teammate: "polly",
      response:
        "## Review complete\n\nThe configured model matches the effective model.\n\n- Routing: passed\n- Return delivery: passed",
      session_url: "/c/preview-only",
    }),
  },
};
export const Failed: Story = {
  args: {
    state: "output-error",
    output: JSON.stringify({ error: "The host is offline. Resume when it reconnects." }),
  },
};
