export type Competitor = {
  ok: boolean;
  table?: number;
  qrValue?: string;
  roundId?: string;
  roundName?: string;
  name?: string;
  teamName?: string;
  members?: string[];
  memberNames?: string;
  category?: string;
  nationality?: string;
  status?: string;
  source?: "api" | "backup" | "manual";
  sourceLabel?: string;
  sourceUrl?: string;
  snapshotDate?: string;
  city?: string;
  username?: string;
  message?: string;
  takenAt?: number;
};

export type Detection = {
  value: string;
  table: number | null;
  decoder: string;
  corners: number[][];
  area: number;
  areaPercent: number;
  hits: number;
  focused: boolean;
};

export type FocusState = {
  focusValue: string | null;
  acquiringValue: string | null;
  acquiringHits: number;
  requiredHits: number;
  locked: boolean;
  missingSeconds: number;
  acquireHits: number;
  hitWindowSeconds: number;
  switchMissingSeconds: number;
  focusHoldSeconds: number;
  switchAreaRatio: number;
};

export type RuntimeState = {
  status: "starting" | "running" | "error";
  sourceId: string;
  sourceLabel: string;
  frameSequence: number;
  resultSequence: number;
  width: number;
  height: number;
  sourceFps: number;
  processedFps: number;
  inferenceMs: number;
  detections: Detection[];
  candidate: Competitor | null;
  onAir: Competitor | null;
  focus: FocusState;
  decoderPipeline: string;
  activeDecoder: string;
  rosterId: string;
  rosterLabel: string;
  roundId: string;
  roundName: string;
  overlayPosition: "left" | "right";
  overlayEnabled: boolean;
  overlayX: number;
  overlayY: number;
  overlayWidth: number;
  overlayHeight: number;
  overlayShowCity: boolean;
  overlayShowUsername: boolean;
  showDiagnosticBoxes: boolean;
  dataMode: "api" | "backup" | "manual";
  competitionId: string;
  competitionIdIsExample: boolean;
  dataStatus: "loading" | "ready" | "stale" | "error";
  dataError: string;
  dataLastRefresh: number;
  dataRefreshSeconds: number;
  dataRoundId: string;
  dataRoundName: string;
  dataCategory: string;
  dataAssignmentCount: number;
  dataTableCount: number;
  dataSourceLabel: string;
  apiRounds: DataRound[];
  backupRounds: BackupRound[];
  message: string;
};

export type DataRound = { id: string; name: string; category: string };
export type BackupRound = DataRound & {
  tables: number;
  assignments: number;
  sourceUrl: string;
  snapshotDate: string;
};

export type AssignmentRow = {
  table: number;
  name: string;
  detail: string;
  status: string;
  ok: boolean;
};

export type SourceInfo = { id: string; label: string; kind: "browser" | "ndi"; active: boolean };
export type RosterInfo = {
  id: string;
  label: string;
  rounds: Array<{ id: string; name: string }>;
  tables: number;
  assignments: number;
};
