// oxlint-disable react/iframe-missing-sandbox -- The separate canvas app needs scripts and its own origin for API calls.
import { useState } from "react";
import { ExternalLinkIcon, RefreshCwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export function SeedanceCanvasPanel({ url }: { url: string }) {
  const [revision, setRevision] = useState(0);
  const externalUrl = new URL(url);
  externalUrl.searchParams.delete("embed");
  return (
    <section
      className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
      aria-label="Seedance canvas"
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
        <span className="min-w-0 flex-1 truncate text-ui font-medium">Seedance V3</span>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Reload canvas"
              onClick={() => setRevision((value) => value + 1)}
            >
              <RefreshCwIcon />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Reload canvas</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button asChild variant="ghost" size="icon">
              <a
                href={externalUrl.href}
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Open canvas in new window"
              >
                <ExternalLinkIcon />
              </a>
            </Button>
          </TooltipTrigger>
          <TooltipContent>Open in new window</TooltipContent>
        </Tooltip>
      </div>
      <iframe
        key={`${url}:${revision}`}
        src={url}
        title="Seedance V3 canvas"
        className="min-h-0 min-w-0 w-full max-w-full flex-1 border-0 bg-background"
        sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"
        referrerPolicy="no-referrer"
      />
    </section>
  );
}
