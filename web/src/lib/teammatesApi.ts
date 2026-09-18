/**
 * Hand-written client for `GET /v1/teammates`, mirroring
 * `agentnexus/server/routes/teammates.py`. Requests go through the Vite `/v1`
 * proxy; the wire is snake_case while the TS surface is camelCase.
 *
 * The roster is the management/display side of the same template registry the
 * new-session picker reads via `/v1/agents`: one row per built-in agent, plus
 * the scheduled tasks (routines) bound to that agent, plus the newest routine
 * activity so a row can render a status without per-agent fetches.
 */

import { authenticatedFetch } from "./identity";

export interface TeammateAgent {
  id: string;
  name: string;
  version: number;
  description: string | null;
  createdAt: number;
  updatedAt: number | null;
  harness: string | null;
  builtin: boolean;
  skills: { name: string; description: string }[];
  terminals: string[];
}

export interface TeammateBot {
  id: string;
  agentId: string;
  name: string;
  description: string | null;
  status: "active" | "archived";
  defaultModel: string | null;
  behaviorMode: "off" | "advisory" | "lean" | "strict";
  homePath: string;
  hostId: string | null;
  createdAt: number;
  updatedAt: number | null;
}

/** One routine bound to a teammate (a scheduled task summary). */
export interface TeammateRoutine {
  id: string;
  name: string;
  state: "active" | "paused";
  rrule: string;
  timezone: string;
  /** Epoch seconds of the last fire, or null before the first fire. */
  lastRunAt: number | null;
  /** Latest run status, or null when the routine has never fired. */
  lastRunStatus: TeammateRoutineRunStatus | null;
  lastRunConversationId: string | null;
  /** ISO timestamp from the server scheduler, or null when not armed. */
  nextRunAt: string | null;
}

export type TeammateRoutineRunStatus =
  "scheduled" | "running" | "succeeded" | "failed" | "skipped" | "incomplete";

/** One roster row: a persistent teammate plus its routines and activity. */
export interface Teammate {
  bot: TeammateBot;
  agent: TeammateAgent;
  routines: TeammateRoutine[];
  routineCount: number;
  lastActivityAt: number | null;
  lastActivityStatus: TeammateRoutineRunStatus | null;
  lastActivityConversationId: string | null;
  primaryConversationId: string | null;
}

interface TeammateBotWire {
  id: string;
  agent_id: string;
  name: string;
  description: string | null;
  status: "active" | "archived";
  default_model: string | null;
  behavior_mode: "off" | "advisory" | "lean" | "strict";
  home_path: string;
  host_id: string | null;
  created_at: number;
  updated_at: number | null;
}

/** One durable memory owned by a teammate bot. */
export interface TeammateMemory {
  id: string;
  agentId: string;
  content: string;
  source: string;
  createdAt: number;
  updatedAt: number | null;
}

interface TeammateAgentWire {
  id: string;
  name: string;
  version: number;
  description: string | null;
  created_at: number;
  updated_at: number | null;
  harness: string | null;
  builtin: boolean;
  skills: { name: string; description: string }[];
  terminals: string[];
}

interface TeammateRoutineWire {
  id: string;
  name: string;
  state: "active" | "paused";
  rrule: string;
  timezone: string;
  last_run_at: number | null;
  last_run_status: TeammateRoutineRunStatus | null;
  last_run_conversation_id: string | null;
  next_run_at: string | null;
}

interface TeammateWire {
  bot: TeammateBotWire;
  agent: TeammateAgentWire;
  routines: TeammateRoutineWire[];
  routine_count: number;
  last_activity_at: number | null;
  last_activity_status: TeammateRoutineRunStatus | null;
  last_activity_conversation_id: string | null;
  primary_conversation_id?: string | null;
}

function botFromWire(wire: TeammateBotWire): TeammateBot {
  return {
    id: wire.id,
    agentId: wire.agent_id,
    name: wire.name,
    description: wire.description,
    status: wire.status,
    defaultModel: wire.default_model,
    behaviorMode: wire.behavior_mode,
    homePath: wire.home_path,
    hostId: wire.host_id,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
  };
}

interface TeammateMemoryWire {
  id: string;
  agent_id: string;
  content: string;
  source: string;
  created_at: number;
  updated_at: number | null;
}

function agentFromWire(wire: TeammateAgentWire): TeammateAgent {
  return {
    id: wire.id,
    name: wire.name,
    version: wire.version,
    description: wire.description,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
    harness: wire.harness,
    builtin: wire.builtin,
    skills: wire.skills,
    terminals: wire.terminals,
  };
}

function routineFromWire(wire: TeammateRoutineWire): TeammateRoutine {
  return {
    id: wire.id,
    name: wire.name,
    state: wire.state,
    rrule: wire.rrule,
    timezone: wire.timezone,
    lastRunAt: wire.last_run_at,
    lastRunStatus: wire.last_run_status,
    lastRunConversationId: wire.last_run_conversation_id,
    nextRunAt: wire.next_run_at,
  };
}

function teammateFromWire(wire: TeammateWire): Teammate {
  return {
    bot: botFromWire(wire.bot),
    agent: agentFromWire(wire.agent),
    routines: wire.routines.map(routineFromWire),
    routineCount: wire.routine_count,
    lastActivityAt: wire.last_activity_at,
    lastActivityStatus: wire.last_activity_status,
    lastActivityConversationId: wire.last_activity_conversation_id,
    primaryConversationId: wire.primary_conversation_id ?? null,
  };
}

function memoryFromWire(wire: TeammateMemoryWire): TeammateMemory {
  return {
    id: wire.id,
    agentId: wire.agent_id,
    content: wire.content,
    source: wire.source,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
  };
}

export class TeammatesApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "TeammatesApiError";
    this.status = status;
  }
}

async function readJsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { error?: { message?: string } };
      if (body.error?.message) message = body.error.message;
    } catch {
      // Non-JSON/empty body: keep the status-line fallback.
    }
    throw new TeammatesApiError(message, res.status);
  }
  return (await res.json()) as T;
}

/** List the caller's teammates roster (owner-scoped server-side). */
export async function listTeammates(): Promise<Teammate[]> {
  const res = await authenticatedFetch("/v1/bots");
  const body = await readJsonOrThrow<{ bots: TeammateWire[] }>(res);
  return (body.bots ?? []).map(teammateFromWire);
}

export async function updateBot(
  botId: string,
  updates: {
    defaultModel?: string | null;
    behaviorMode?: TeammateBot["behaviorMode"];
    homePath?: string;
  },
): Promise<TeammateBot> {
  const res = await authenticatedFetch(`/v1/bots/${encodeURIComponent(botId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...(updates.defaultModel !== undefined ? { default_model: updates.defaultModel } : {}),
      ...(updates.behaviorMode !== undefined ? { behavior_mode: updates.behaviorMode } : {}),
      ...(updates.homePath !== undefined ? { home_path: updates.homePath } : {}),
    }),
  });
  const body = await readJsonOrThrow<{ bot: TeammateBotWire }>(res);
  return botFromWire(body.bot);
}

/** List one teammate's memories, newest first. */
export async function listTeammateMemories(agentId: string): Promise<TeammateMemory[]> {
  const res = await authenticatedFetch(`/v1/teammates/${encodeURIComponent(agentId)}/memories`);
  const body = await readJsonOrThrow<{ memories: TeammateMemoryWire[] }>(res);
  return (body.memories ?? []).map(memoryFromWire);
}

/** Add a memory to one teammate. */
export async function createTeammateMemory(
  agentId: string,
  content: string,
): Promise<TeammateMemory> {
  const res = await authenticatedFetch(`/v1/teammates/${encodeURIComponent(agentId)}/memories`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  const body = await readJsonOrThrow<{ memory: TeammateMemoryWire }>(res);
  return memoryFromWire(body.memory);
}

/** Edit one teammate memory. */
export async function updateTeammateMemory(
  agentId: string,
  memoryId: string,
  content: string,
): Promise<TeammateMemory> {
  const res = await authenticatedFetch(
    `/v1/teammates/${encodeURIComponent(agentId)}/memories/${encodeURIComponent(memoryId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    },
  );
  const body = await readJsonOrThrow<{ memory: TeammateMemoryWire }>(res);
  return memoryFromWire(body.memory);
}

/** Delete one teammate memory. */
export async function deleteTeammateMemory(agentId: string, memoryId: string): Promise<void> {
  const res = await authenticatedFetch(
    `/v1/teammates/${encodeURIComponent(agentId)}/memories/${encodeURIComponent(memoryId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { error?: { message?: string } };
      if (body.error?.message) message = body.error.message;
    } catch {
      // Non-JSON/empty body: keep the status-line fallback.
    }
    throw new TeammatesApiError(message, res.status);
  }
}
