// Dialog for viewing and editing one teammate bot's durable memories.

import { useEffect, useState } from "react";
import {
  BrainIcon,
  Loader2Icon,
  PencilIcon,
  SaveIcon,
  Trash2Icon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import {
  useCreateTeammateMemory,
  useDeleteTeammateMemory,
  useTeammateMemories,
  useUpdateTeammateMemory,
} from "@/hooks/useTeammateMemories";
import type { TeammateAgent } from "@/lib/teammatesApi";

export function TeammateMemoryDialog({
  agent,
  open,
  onOpenChange,
}: {
  agent: TeammateAgent | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const agentId = agent?.id ?? "";
  const { data: memories, isLoading, isError } = useTeammateMemories(agentId || null);
  const createMutation = useCreateTeammateMemory(agentId);
  const updateMutation = useUpdateTeammateMemory(agentId);
  const deleteMutation = useDeleteTeammateMemory(agentId);
  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setDraft("");
      setEditingId(null);
      setError(null);
    }
  }, [open, agentId]);

  function startEdit(memoryId: string, content: string) {
    setEditingId(memoryId);
    setDraft(content);
    setError(null);
  }

  function cancelEdit() {
    setEditingId(null);
    setDraft("");
    setError(null);
  }

  async function handleSave() {
    const content = draft.trim();
    if (!content || agentId === "") return;
    setError(null);
    try {
      if (editingId === null) {
        await createMutation.mutateAsync(content);
      } else {
        await updateMutation.mutateAsync({ memoryId: editingId, content });
      }
      cancelEdit();
    } catch {
      setError("Couldn't save that memory.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <BrainIcon className="size-4" />
            {agent?.name ?? "Teammate"} memories
          </DialogTitle>
        </DialogHeader>

        {error && <p className="text-ui text-destructive">{error}</p>}

        <div className="flex-1 space-y-3 overflow-y-auto pr-1">
          {isError ? (
            <div
              role="alert"
              className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-ui"
            >
              <TriangleAlertIcon className="size-4 shrink-0 text-destructive" />
              <span className="flex-1">Couldn't load memories.</span>
            </div>
          ) : isLoading ? (
            <div className="flex items-center gap-2 py-8 text-ui text-muted-foreground">
              <Loader2Icon className="size-4 animate-spin" />
              Loading memories…
            </div>
          ) : (memories ?? []).length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">No memories yet.</p>
          ) : (
            (memories ?? []).map((memory) => (
              <div
                key={memory.id}
                data-testid={`memory-${memory.id}`}
                className="rounded-lg border border-border/70 bg-background p-3"
              >
                {editingId === memory.id ? (
                  <Textarea
                    value={draft}
                    onChange={(event) => setDraft(event.currentTarget.value)}
                    maxLength={20_000}
                    className="min-h-20"
                    data-testid="memory-draft"
                  />
                ) : (
                  <p className="text-ui leading-5 whitespace-pre-wrap">{memory.content}</p>
                )}
                <div className="mt-2 flex items-center justify-between gap-2">
                  <span className="text-xs text-muted-foreground">
                    {new Date(memory.createdAt * 1000).toLocaleString()}
                  </span>
                  <div className="flex items-center gap-1">
                    {editingId === memory.id ? (
                      <>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={cancelEdit}
                          data-testid={`cancel-memory-${memory.id}`}
                        >
                          <XIcon className="size-3.5" />
                          Cancel
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => void handleSave()}
                          disabled={updateMutation.isPending || draft.trim() === ""}
                          data-testid={`save-memory-${memory.id}`}
                        >
                          <SaveIcon className="size-3.5" />
                          Save
                        </Button>
                      </>
                    ) : (
                      <>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Edit memory ${memory.id}`}
                          title="Edit"
                          onClick={() => startEdit(memory.id, memory.content)}
                          data-testid={`edit-memory-${memory.id}`}
                          componentId={`teammates.memory.${memory.id}.edit`}
                        >
                          <PencilIcon className="size-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Delete memory ${memory.id}`}
                          title="Delete"
                          disabled={deleteMutation.isPending}
                          onClick={() => deleteMutation.mutate(memory.id)}
                          data-testid={`delete-memory-${memory.id}`}
                          componentId={`teammates.memory.${memory.id}.delete`}
                        >
                          <Trash2Icon className="size-3.5" />
                        </Button>
                      </>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>

        {editingId === null && (
          <div className="space-y-2 border-t border-border/60 pt-3">
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.currentTarget.value)}
              placeholder="Add a memory"
              maxLength={20_000}
              className="min-h-20"
              data-testid="memory-composer"
            />
            <div className="flex justify-end">
              <Button
                onClick={() => void handleSave()}
                disabled={agentId === "" || createMutation.isPending || draft.trim() === ""}
                data-testid="save-new-memory"
                componentId="teammates.memory.create"
              >
                <SaveIcon className="size-3.5" />
                Save
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
