import { useEffect, useRef, useState } from "react";
import { CompetitorOverlay, DetectionLayer, EditableCompetitorOverlay } from "./OverlayStage";
import type { OverlayLayout } from "./OverlayStage";
import type { AssignmentRow, RuntimeState, SourceInfo } from "./types";


type ManualPreset = { id: string; table: string; name: string; detail: string; roundName: string };


const initialState: RuntimeState = {
  status: "starting", sourceId: "browser", sourceLabel: "Browser camera",
  frameSequence: 0, resultSequence: 0, width: 1920, height: 1080,
  sourceFps: 0, processedFps: 0, inferenceMs: 0, detections: [], candidate: null, onAir: null,
  focus: {
    focusValue: null, acquiringValue: null, acquiringHits: 0, requiredHits: 3,
    locked: false, missingSeconds: 0, acquireHits: 3, hitWindowSeconds: 1,
    switchMissingSeconds: .65, focusHoldSeconds: 2, switchAreaRatio: 1.35,
  },
  decoderPipeline: "WeChatQRCode + super-resolution → ZXing-C++ → OpenCV",
  activeDecoder: "Waiting", rosterId: "sample_competitors", rosterLabel: "Sample Competitors",
  roundId: "round_1", roundName: "Individual Round 1", overlayPosition: "left",
  overlayEnabled: true, overlayX: 30, overlayY: 860, overlayWidth: 800, overlayHeight: 190,
  overlayShowCity: true, overlayShowUsername: true, showDiagnosticBoxes: true,
  dataMode: "api", competitionId: "019f7652-3968-7176-b00c-49b7deab1bb4", competitionIdIsExample: false,
  dataStatus: "loading", dataError: "", dataLastRefresh: 0, dataRefreshSeconds: 15,
  dataRoundId: "", dataRoundName: "Loading rounds…", dataCategory: "", dataAssignmentCount: 0,
  dataTableCount: 0, dataSourceLabel: "Live tournament API", apiRounds: [], backupRounds: [],
  message: "Connecting to QR runtime…",
};


export function App() {
  const overlay = location.pathname.startsWith("/overlay");
  const [state, setState] = useState(initialState);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    document.documentElement.classList.toggle("overlay-page", overlay);
    document.body.classList.toggle("overlay-page", overlay);
    return () => {
      document.documentElement.classList.remove("overlay-page");
      document.body.classList.remove("overlay-page");
    };
  }, [overlay]);

  useEffect(() => {
    let retry: number | undefined;
    let socket: WebSocket | undefined;
    let stopped = false;
    const connect = () => {
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}://${location.host}/ws`);
      socket.onopen = () => setConnected(true);
      socket.onmessage = (event) => setState(JSON.parse(event.data) as RuntimeState);
      socket.onclose = () => {
        setConnected(false);
        if (!stopped) retry = window.setTimeout(connect, 1000);
      };
    };
    connect();
    return () => {
      stopped = true;
      if (retry) window.clearTimeout(retry);
      socket?.close();
    };
  }, []);

  if (overlay) return <BroadcastOverlay state={state} connected={connected}/>;
  return <ControlRoom state={state} connected={connected}/>;
}


function ControlRoom({ state, connected }: { state: RuntimeState; connected: boolean }) {
  const [theme, setTheme] = useState<"light" | "dark">(() => localStorage.getItem("pjm-qr-dashboard-theme") === "dark" ? "dark" : "light");
  const [sources, setSources] = useState<SourceInfo[]>([{ id: "browser", label: "Browser camera / phone", kind: "browser", active: true }]);
  const [ndiAvailable, setNdiAvailable] = useState(false);
  const [ndiError, setNdiError] = useState("");
  const [competitionId, setCompetitionId] = useState(state.competitionId);
  const [dataActionError, setDataActionError] = useState("");
  const [assignmentRows, setAssignmentRows] = useState<AssignmentRow[]>([]);
  const [assignmentLoading, setAssignmentLoading] = useState(false);
  const [cameraRunning, setCameraRunning] = useState(false);
  const [cameraMessage, setCameraMessage] = useState("Browser camera stopped");
  const [manualTable, setManualTable] = useState("1");
  const [manualName, setManualName] = useState("Manual Competitor");
  const [manualDetail, setManualDetail] = useState("");
  const [manualRoundName, setManualRoundName] = useState("Manual Override");
  const [manualPresetId, setManualPresetId] = useState("");
  const [manualPresets, setManualPresets] = useState<ManualPreset[]>(() => {
    try { return JSON.parse(localStorage.getItem("pjm-qr-manual-presets") || "[]") as ManualPreset[]; }
    catch { return []; }
  });
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const sendingRef = useRef(false);
  const captureCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const browserClientRef = useRef(crypto.randomUUID());
  const competitionSyncedRef = useRef(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => localStorage.setItem("pjm-qr-dashboard-theme", theme), [theme]);
  useEffect(() => localStorage.setItem("pjm-qr-manual-presets", JSON.stringify(manualPresets)), [manualPresets]);
  useEffect(() => {
    if (connected && !competitionSyncedRef.current) {
      setCompetitionId(state.competitionId);
      competitionSyncedRef.current = true;
    }
  }, [connected, state.competitionId]);

  const refreshSources = async () => {
    try {
      const data = await (await fetch("/api/sources")).json() as { sources: SourceInfo[]; ndiAvailable: boolean; ndiError: string };
      setSources(data.sources);
      setNdiAvailable(data.ndiAvailable);
      setNdiError(data.ndiError || "");
    } catch { /* WebSocket status covers backend failures. */ }
  };
  useEffect(() => {
    void refreshSources();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refreshSources();
    }, 5000);
    return () => window.clearInterval(timer);
  }, []);

  const post = async (path: string, body?: unknown) => {
    const response = await fetch(path, {
      method: "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return response.json();
  };

  const refreshAssignments = async () => {
    setAssignmentLoading(true);
    try {
      const data = await (await fetch("/api/event-data/assignments")).json() as { assignments: AssignmentRow[] };
      setAssignmentRows(data.assignments);
    } catch { setAssignmentRows([]); }
    finally { setAssignmentLoading(false); }
  };

  useEffect(() => {
    if (state.dataMode !== "manual") void refreshAssignments();
  }, [state.dataMode, state.dataRoundId, state.dataLastRefresh]);

  const selectSource = async (sourceId: string) => {
    await post("/api/source", { sourceId });
    if (sourceId !== "browser") stopCamera();
  };

  const stopCamera = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setCameraRunning(false);
    setCameraMessage("Browser camera stopped");
  };

  const startCamera = async () => {
    try {
      if (state.sourceId !== "browser") await selectSource("browser");
      stopCamera();
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      streamRef.current = stream;
      if (!videoRef.current) return;
      videoRef.current.srcObject = stream;
      await videoRef.current.play();
      setCameraRunning(true);
      const settings = stream.getVideoTracks()[0]?.getSettings();
      setCameraMessage(`${settings?.width ?? "?"} × ${settings?.height ?? "?"} browser camera`);
    } catch (error) {
      setCameraRunning(false);
      setCameraMessage(`Camera unavailable: ${String(error)}`);
    }
  };

  const sendImage = async (image: CanvasImageSource, sourceWidth: number, sourceHeight: number) => {
    if (sendingRef.current) return;
    sendingRef.current = true;
    try {
      const scale = Math.min(1, 1920 / Math.max(1, sourceWidth));
      const canvas = captureCanvasRef.current ?? document.createElement("canvas");
      captureCanvasRef.current = canvas;
      canvas.width = Math.max(1, Math.round(sourceWidth * scale));
      canvas.height = Math.max(1, Math.round(sourceHeight * scale));
      canvas.getContext("2d")?.drawImage(image, 0, 0, canvas.width, canvas.height);
      await post("/api/browser-frame", {
        clientId: browserClientRef.current,
        image: canvas.toDataURL("image/jpeg", .80),
      });
    } finally {
      sendingRef.current = false;
    }
  };

  useEffect(() => {
    const timer = window.setInterval(() => {
      const video = videoRef.current;
      if (cameraRunning && video && video.readyState >= 2) {
        void sendImage(video, video.videoWidth, video.videoHeight);
      }
    }, 80);
    return () => window.clearInterval(timer);
  }, [cameraRunning, state.sourceId]);

  const usePhoto = async (file: File) => {
    if (state.sourceId !== "browser") await selectSource("browser");
    const url = URL.createObjectURL(file);
    const image = new Image();
    try {
      await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = reject; image.src = url; });
      await sendImage(image, image.naturalWidth, image.naturalHeight);
    } finally {
      URL.revokeObjectURL(url);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const runDataAction = async (path: string, body?: unknown) => {
    setDataActionError("");
    const result = await post(path, body);
    if (result.error) setDataActionError(result.error);
    return result;
  };

  const saveManualPreset = () => {
    const preset: ManualPreset = {
      id: crypto.randomUUID(), table: manualTable, name: manualName,
      detail: manualDetail, roundName: manualRoundName,
    };
    setManualPresets((items) => [...items, preset].slice(-20));
    setManualPresetId(preset.id);
  };

  const loadManualPreset = (id: string) => {
    setManualPresetId(id);
    const preset = manualPresets.find((item) => item.id === id);
    if (!preset) return;
    setManualTable(preset.table);
    setManualName(preset.name);
    setManualDetail(preset.detail);
    setManualRoundName(preset.roundName);
  };

  const candidateReady = Boolean(state.candidate?.ok);
  const statusLabel = state.focus.locked ? "LOCKED" : candidateReady ? "READY" : state.focus.acquiringValue ? "ACQUIRING" : "SEARCHING";

  return <main className={`control-room theme-${theme}`}>
    <video ref={videoRef} className="browser-source-video" playsInline muted/>
    <input ref={fileRef} className="hidden-input" type="file" accept="image/*" capture="environment" onChange={(event) => { const file = event.target.files?.[0]; if (file) void usePhoto(file); }}/>
    <section className="workspace">
      <div className="main-column">
        <section className="monitor-panel panel">
          <div className="panel-heading">
            <div className="source-picker-group">
              <label className="header-control source-header-control"><span className="label">SOURCE · {ndiAvailable ? "NDI READY" : "BROWSER"}</span>
                <select aria-label="Video source" value={state.sourceId} onChange={(event) => void selectSource(event.target.value)}>
                  {!sources.some((source) => source.id === state.sourceId) && <option value={state.sourceId}>{state.sourceLabel}</option>}
                  {sources.map((source) => <option key={source.id} value={source.id}>{source.kind === "ndi" ? "NDI · " : ""}{source.label}</option>)}
                </select>
              </label>
              {state.sourceId === "browser" && <>
                <button type="button" className={`test-mode-button ${cameraRunning ? "active" : ""}`} onClick={() => void startCamera()}>{cameraRunning ? "RESTART CAMERA" : "START CAMERA"}</button>
                <button type="button" className="test-mode-button" onClick={() => fileRef.current?.click()}>USE PHOTO</button>
              </>}
            </div>
            <div className="heading-status">
              <span className="source-format">{state.width} × {state.height} · {state.sourceFps.toFixed(1)} fps</span>
              <span className={`connection ${connected ? "online" : "offline"}`}><i/>{connected ? "CONNECTED" : "OFFLINE"}</span>
            </div>
          </div>
          <div className="monitor">
            <img src="/api/preview.mjpg" alt="QR camera feed"/>
            {state.showDiagnosticBoxes && <DetectionLayer state={state}/>} 
            <EditableCompetitorOverlay state={state} onLayoutCommit={(layout: OverlayLayout) => void post("/api/overlay-settings", layout)}/>
            {!connected && <div className="monitor-message">Start the PJM QR runtime to view the feed.</div>}
          </div>
          <div className="transport-strip">
            <span className="live-dot"/> LIVE QR ANALYSIS
            <span className={`candidate-status status-${statusLabel.toLowerCase()}`}>{statusLabel}</span>
            <span className="footer-performance">{state.inferenceMs.toFixed(0)} ms · {state.processedFps.toFixed(1)} processed fps · {state.activeDecoder}</span>
            <span>Frame {state.frameSequence.toLocaleString()}</span>
          </div>
        </section>

      </div>

      <aside className="metrics-column">
        <section className={`panel candidate-card ${candidateReady ? "ready" : ""}`}>
          <div className="section-title"><span className="label">DETECTED COMPETITOR</span><strong>{state.overlayEnabled ? "OVERLAY ON" : statusLabel}</strong></div>
          {state.candidate?.ok ? <>
            <div className="candidate-table">TABLE {state.candidate.table}</div>
            <div className="candidate-name">{state.candidate.name}</div>
            {state.candidate.memberNames && state.candidate.memberNames !== state.candidate.name && <div className="candidate-meta">{state.candidate.memberNames}</div>}
            <div className="candidate-round">{state.candidate.roundName} · {state.candidate.sourceLabel}</div>
          </> : <div className="candidate-empty">{state.candidate?.message ?? (state.focus.acquiringValue ? `Confirming detection ${state.focus.acquiringHits}/${state.focus.requiredHits}` : "Point the active feed at a table QR code.")}</div>}
          <div className="candidate-actions">
            <button className={`overlay-toggle-button ${state.overlayEnabled ? "on" : ""}`} type="button" onClick={() => void post("/api/overlay-enabled", { enabled: !state.overlayEnabled })}>{state.overlayEnabled ? "OVERLAY OFF" : "OVERLAY ON"}</button>
            <button type="button" className={state.focus.locked ? "active" : ""} disabled={!state.candidate} onClick={() => void post("/api/candidate/lock", { locked: !state.focus.locked })}>{state.focus.locked ? "UNLOCK" : "LOCK"}</button>
            <button type="button" onClick={() => void post("/api/candidate/clear")}>RESET</button>
          </div>
        </section>

        <section className="panel controls event-controls">
          <div className="section-title"><span className="label">EVENT DATA</span><strong>{state.dataAssignmentCount} ASSIGNED</strong></div>
          <div className="data-mode-switch" role="group" aria-label="Roster data source">
            <button type="button" className={state.dataMode === "api" ? "active" : ""} onClick={() => void runDataAction("/api/event-data/mode", { mode: "api" })}>LIVE API</button>
            <button type="button" className={state.dataMode === "backup" ? "active" : ""} onClick={() => void runDataAction("/api/event-data/mode", { mode: "backup" })}>BACKUP CSV</button>
            <button type="button" className={state.dataMode === "manual" ? "active manual" : ""} onClick={() => void runDataAction("/api/event-data/mode", { mode: "manual" })}>MANUAL</button>
          </div>
          {state.dataMode === "api" ? <>
            <label>Competition UUID <InfoTip text="The tournament system uses this ID to identify the event. Paste the live Portland competition UUID here; no API key or password is required."/>
              <input className="competition-id-input" value={competitionId} spellCheck={false} onChange={(event) => setCompetitionId(event.target.value)} />
            </label>
            <button className="import-button" type="button" onClick={() => void runDataAction("/api/event-data/competition", { competitionId })}>LOAD COMPETITION</button>
            {state.competitionIdIsExample && <p className="source-warning">The supplied UUID is an example/test competition. Replace it with the live PJM competition UUID before broadcast.</p>}
            <label>Live round<select value={state.dataRoundId} disabled={!state.apiRounds.length} onChange={(event) => void runDataAction("/api/round", { roundId: event.target.value })}>
              {!state.apiRounds.length && <option value="">Loading tournament rounds…</option>}
              {state.apiRounds.map((round) => <option key={round.id} value={round.id}>{round.name}</option>)}
            </select></label>
            <button type="button" onClick={() => void runDataAction("/api/event-data/refresh")}>REFRESH NOW</button>
          </> : state.dataMode === "backup" ? <>
            <label>Backup roster snapshot <InfoTip text="These CSV files were extracted from the official preliminary schedule PDFs on August 10. They are never combined with live API data."/>
              <select value={state.dataRoundId} onChange={(event) => void runDataAction("/api/round", { roundId: event.target.value })}>
                {state.backupRounds.map((round) => <option key={round.id} value={round.id}>{round.name}</option>)}
              </select>
            </label>
            <p className="backup-note">Fallback only · PDF snapshot {state.backupRounds.find((round) => round.id === state.dataRoundId)?.snapshotDate ?? "2026-08-10"}. It may differ from current assignments.</p>
          </> : <>
            <p className="manual-note">Worst-case broadcast fallback. This bypasses tournament data and puts the label below directly on air.</p>
            <label>Saved quick label<select value={manualPresetId} onChange={(event) => loadManualPreset(event.target.value)}>
              <option value="">Choose a saved label…</option>
              {manualPresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name} · Table {preset.table}</option>)}
            </select></label>
            <div className="manual-preset-actions">
              <button type="button" onClick={saveManualPreset} disabled={!manualName.trim()}>SAVE CURRENT</button>
              <button type="button" disabled={!manualPresetId} onClick={() => {
                setManualPresets((items) => items.filter((item) => item.id !== manualPresetId));
                setManualPresetId("");
              }}>DELETE SAVED</button>
            </div>
            <div className="manual-form-grid">
              <label>Table / reference<input type="number" min="1" max="10000" value={manualTable} onChange={(event) => setManualTable(event.target.value)}/></label>
              <label>Round / context<input value={manualRoundName} maxLength={120} onChange={(event) => setManualRoundName(event.target.value)}/></label>
            </div>
            <label>Primary on-air label<input value={manualName} maxLength={160} placeholder="Competitor or team name" onChange={(event) => setManualName(event.target.value)}/></label>
            <label>Optional second line<input value={manualDetail} maxLength={240} placeholder="Members, city, note, etc." onChange={(event) => setManualDetail(event.target.value)}/></label>
            <button className="manual-apply-button" type="button" disabled={!manualName.trim()} onClick={() => void runDataAction("/api/event-data/manual", {
              table: Number(manualTable), name: manualName, detail: manualDetail, roundName: manualRoundName,
            })}>APPLY MANUAL LABEL</button>
          </>}
          {(dataActionError || state.dataError) && <p className="error-note">{dataActionError || state.dataError}</p>}
          <div className={`data-health data-${state.dataStatus}`}><i/><span>{state.dataSourceLabel}</span><strong>{state.dataStatus.toUpperCase()}</strong></div>
          <div className="active-round-banner"><span>ACTIVE ROUND · {state.dataCategory || "—"}</span><strong>{state.dataRoundName}</strong><small>{state.dataLastRefresh ? `Updated ${new Date(state.dataLastRefresh * 1000).toLocaleTimeString()}` : "Waiting for first complete load"}</small></div>
          {state.dataMode !== "manual" && <div className="assignment-inspector">
            <div className="assignment-inspector-heading"><span>ROUND ASSIGNMENTS</span><strong>{assignmentLoading ? "LOADING…" : `${assignmentRows.length} ROWS`}</strong></div>
            <div className="assignment-table" role="table" aria-label="Active round assignments">
              <div className="assignment-row header" role="row"><span>TABLE</span><span>ENTRY / MEMBERS</span><span>STATUS</span></div>
              {assignmentRows.map((row) => <div className={`assignment-row ${row.ok ? "" : "unassigned"}`} role="row" key={row.table}>
                <strong>{row.table}</strong><span><b>{row.name || "Unassigned"}</b>{row.detail && row.detail !== row.name && <small>{row.detail}</small>}</span><em>{row.status}</em>
              </div>)}
              {!assignmentLoading && !assignmentRows.length && <p className="assignment-empty">No assignments returned for this round.</p>}
            </div>
          </div>}
        </section>

        <details className="panel compact-details" open>
          <summary><span className="label">FOCUS BEHAVIOR</span><strong>{state.focus.acquireHits} HIT CONFIRM</strong></summary>
          <div className="compact-detail-body focus-controls">
            <NumberSetting label="Confirmation hits" help="How many successful reads are required before a QR becomes the active candidate. Higher values reduce accidental switches but take slightly longer to confirm." value={state.focus.acquireHits} min={1} max={12} step={1} onChange={(value) => void post("/api/focus", { acquireHits: value })}/>
            <NumberSetting label="Hit window" help="The confirmation reads must happen within this many seconds. Reads older than this window no longer count toward confirmation." suffix="s" value={state.focus.hitWindowSeconds} min={.2} max={5} step={.1} onChange={(value) => void post("/api/focus", { hitWindowSeconds: value })}/>
            <NumberSetting label="Switch after missing" help="How long the active QR may be absent before another visible QR is allowed to replace it. This prevents quick camera motion from changing focus immediately." suffix="s" value={state.focus.switchMissingSeconds} min={0} max={5} step={.05} onChange={(value) => void post("/api/focus", { switchMissingSeconds: value })}/>
            <NumberSetting label="Dropout hold" help="How long the system remembers the active QR through missed or flickering detections. The candidate stays stable during this grace period." suffix="s" value={state.focus.focusHoldSeconds} min={.2} max={15} step={.1} onChange={(value) => void post("/api/focus", { focusHoldSeconds: value })}/>
            <NumberSetting label="Immediate switch size" help="A competing QR can switch focus immediately if its visible area is this many times larger than the current one. This helps the camera operator intentionally frame a new table." suffix="×" value={state.focus.switchAreaRatio} min={1} max={4} step={.05} onChange={(value) => void post("/api/focus", { switchAreaRatio: value })}/>
            <p>Larger codes win. A competing code must be this much larger, or the current code must be missing, before focus changes.</p>
          </div>
        </details>

        <details className="panel compact-details" open>
          <summary><span className="label">DIAGNOSTICS</span><strong>{state.detections.length} QR VISIBLE</strong></summary>
          <div className="compact-detail-body diagnostic-list">
            <button type="button" className={`diagnostic-box-button ${state.showDiagnosticBoxes ? "on" : ""}`} onClick={() => void post("/api/diagnostic-settings", { showBoxes: !state.showDiagnosticBoxes })}>QR BOXES: {state.showDiagnosticBoxes ? "ON" : "OFF"}</button>
            <p className="operator-only-note">Operator preview only. Active QR is thick green; other visible QRs are thin red. These boxes are never sent to the broadcast overlay.</p>
            <div><span>Runtime</span><strong>{state.status.toUpperCase()}</strong></div>
            <div><span>Source</span><strong>{state.sourceLabel}</strong></div>
            <div><span>Input</span><strong>{state.width}×{state.height} · {state.sourceFps.toFixed(1)} fps</strong></div>
            <div><span>Processing</span><strong>{state.inferenceMs.toFixed(0)} ms · {state.processedFps.toFixed(1)} fps</strong></div>
            <div><span>Decoder</span><strong>{state.activeDecoder}</strong></div>
            <div><span>Focused dropout</span><strong>{state.focus.missingSeconds.toFixed(2)} s</strong></div>
            <div><span>Candidate lock</span><strong>{state.focus.locked ? "LOCKED" : "AUTO"}</strong></div>
            {state.detections.slice(0, 4).map((item, index) => <div key={`${item.value}-${index}`}><span>{item.table ? `Table ${item.table}` : "Unknown QR"} · {item.areaPercent.toFixed(2)}%</span><strong>{item.hits}/{state.focus.requiredHits} · {item.decoder}</strong></div>)}
            <p className="diagnostic-message">{state.message}</p>
            {state.sourceId === "browser" && <p className="diagnostic-message">{cameraMessage}</p>}
            {!ndiAvailable && ndiError && <p className="diagnostic-message">NDI: {ndiError}</p>}
          </div>
        </details>

        <section className="panel controls overlay-controls">
          <div className="section-title"><span className="label">OVERLAY CONTENT</span><strong>SVG · 1080P</strong></div>
          <p className="overlay-edit-help">Click the on-screen graphic to select it, then drag to move or use its blue corner to resize. Click elsewhere to hide the editing guides.</p>
          <div className="overlay-preset-row"><button type="button" onClick={() => void post("/api/overlay-settings", { position: "left" })}>RESET LEFT</button><button type="button" onClick={() => void post("/api/overlay-settings", { position: "right" })}>RESET RIGHT</button></div>
          <div className="overlay-layout-readout">X {Math.round(state.overlayX)} · Y {Math.round(state.overlayY)} · {Math.round(state.overlayWidth)} × {Math.round(state.overlayHeight)}</div>
          <Toggle label="Show member names" checked={state.overlayShowCity} onChange={(value) => void post("/api/overlay-settings", { showCity: value })}/>
          <Toggle label="Show nationality" checked={state.overlayShowUsername} onChange={(value) => void post("/api/overlay-settings", { showUsername: value })}/>
        </section>

        <OverlayLinks/>
      </aside>
    </section>
    <button className="dashboard-theme-toggle" type="button" onClick={() => setTheme((current) => current === "light" ? "dark" : "light")}><span>{theme === "light" ? "◐" : "☀"}</span>{theme === "light" ? "Dark" : "Light"}</button>
  </main>;
}


function NumberSetting({ label, help, value, suffix = "", min, max, step, onChange }: { label: string; help: string; value: number; suffix?: string; min: number; max: number; step: number; onChange: (value: number) => void }) {
  return <label className="number-setting"><span className="setting-label">{label}<InfoTip text={help}/></span><span><input type="number" value={value} min={min} max={max} step={step} onChange={(event) => onChange(Number(event.target.value))}/>{suffix}</span></label>;
}


function InfoTip({ text }: { text: string }) {
  return <span className="info-tip" tabIndex={0} role="img" aria-label={`Information: ${text}`}>i<span className="info-tooltip" role="tooltip">{text}</span></span>;
}


function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="toggle-row"><span>{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)}/></label>;
}


function OverlayLinks() {
  const [base, setBase] = useState(location.origin);
  const [bases, setBases] = useState([location.origin]);
  const [copied, setCopied] = useState("");
  useEffect(() => {
    void fetch("/api/local-info").then((response) => response.json()).then((data: { addresses: string[]; uiPort: number }) => {
      const protocol = location.protocol === "https:" ? "https" : "http";
      const values = Array.from(new Set([location.origin, ...data.addresses.map((address) => `${protocol}://${address}:${data.uiPort}`)]));
      setBases(values);
      if (["localhost", "127.0.0.1"].includes(location.hostname) && values.length > 1) setBase(values[1]);
    });
  }, []);
  const links = [
    { id: "main", label: "vMix transparent browser overlay", url: `${base}/overlay/main` },
    { id: "svg", label: "Raw dynamic SVG snapshot", url: `${base}/api/overlay.svg` },
  ];
  const copy = async (id: string, value: string) => {
    await navigator.clipboard.writeText(value);
    setCopied(id);
    window.setTimeout(() => setCopied(""), 1400);
  };
  return <details className="panel compact-details output-card" open>
    <summary><span className="label">VMIX OUTPUTS</span><strong>TRANSPARENT</strong></summary>
    <div className="compact-detail-body">
      {bases.length > 1 && <label className="output-address">Address for AV crew<select value={base} onChange={(event) => setBase(event.target.value)}>{bases.map((value) => <option key={value}>{value}</option>)}</select></label>}
      <div className="overlay-link-list">{links.map((link) => <div key={link.id}><span>{link.label}</span><code>{link.url}</code><button type="button" onClick={() => void copy(link.id, link.url)}>{copied === link.id ? "Copied" : "Copy"}</button></div>)}</div>
      <p>Use the browser overlay URL in vMix at 1920×1080. The SVG endpoint is useful for diagnostics or systems that poll images.</p>
    </div>
  </details>;
}


function BroadcastOverlay({ state, connected }: { state: RuntimeState; connected: boolean }) {
  return <main className="broadcast-overlay">
    <CompetitorOverlay state={state}/>
    {!connected && <div className="overlay-warning">PJM QR runtime disconnected</div>}
  </main>;
}
