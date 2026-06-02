type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
type CSSVars = React.CSSProperties & Record<string, string | number>;

type AgentStatus = 'idle' | 'working';
type NodeStatus = 'unborn' | 'queued' | 'claimed' | 'submitted' | 'verified' | 'rejected' | 'abandoned';

interface ResearchMeta {
  baseline?: number;
  best?: number | null;
  direction?: string;
  [key: string]: any;
}

interface ResearchAgent {
  id: string;
  role: string;
  spawnedAt: number;
  retiredAt?: number | null;
  [key: string]: any;
}

interface ResearchNode {
  id: string;
  tProposed: number;
  tClaimed?: number | null;
  tSubmitted?: number | null;
  tVerified?: number | null;
  abandoned?: boolean;
  outcome?: string;
  score?: number | null;
  impl?: string;
  verifier?: string;
  proposer?: string;
  candidate?: string;
  subId?: string;
  [key: string]: any;
}

interface ResearchEvent {
  t: number;
  kind?: string;
  agent?: string;
  [key: string]: any;
}

interface AgentActivity {
  id: string;
  role: string;
  alive: boolean;
  status: AgentStatus;
  item: string | null;
  kind: string | null;
}

interface ResearchWorld {
  meta: ResearchMeta;
  nodes: ResearchNode[];
  agents: ResearchAgent[];
  events: ResearchEvent[];
  fns?: {
    statusAt(node: ResearchNode, time: number): NodeStatus;
    bornCount(time: number): number;
    eventCount(time: number): number;
    agentActivity(time: number): Record<string, AgentActivity>;
    frontierAt(time: number): number | null;
    fitBin(score: number | null | undefined): number | null;
  };
  [key: string]: any;
}

interface AutoresearchUI {
  apiUrl(path: string, task?: string, params?: Record<string, string | number | boolean>): string;
  currentTask(): string;
  fmt(value: unknown): string;
  mmss(value: unknown): string;
  queryParam(name: string): string;
}

interface Window {
  APP?: ResearchWorld;
  RUNS?: any[];
  FRONTEND_API_URL?: string;
  AutoresearchUI: AutoresearchUI;
  __AUTORESEARCH_JOURNAL?: string;
  __AUTORESEARCH_APPLY_PAYLOAD?: (payload: any, meta?: any) => void;
  __AUTORESEARCH_PENDING_PAYLOAD?: any;
  __AUTORESEARCH_PENDING_DATA?: any;
  __AUTORESEARCH_EVENTS?: EventSource;
  __AUTORESEARCH_DEV_EVENTS?: EventSource;
  appWorld?: (payload: ResearchWorld) => ResearchWorld;
  buildWorld?: (seed: number, params?: Record<string, unknown>) => any;
  EvoTree?: any;
  TREE_LAYOUT?: () => any;
  InspectorPanel?: any;
  TeamPanel?: any;
  HypothesesPanel?: any;
  ActivityPanel?: any;
  TweaksPanel?: any;
  TweakSection?: any;
  TweakRadio?: any;
  TweakColor?: any;
  TweakToggle?: any;
  useTweaks?: any;
  SBSection?: any;
  evoRoleCol?: any;
  EVO_ROLE_LABEL?: Record<string, string>;
  evoMMSS?: (value: unknown) => string;
  EVO_RUNS?: any[];
  EVO_RUN_BY_ID?: Record<string, any>;
  AutoresearchVisualizations?: Record<string, any>;
  RunPlayback?: any;
  [key: string]: any;
}

declare namespace ReactDOM {
  function createRoot(el: Element | DocumentFragment | null): { render(node: React.ReactNode): void };
}
