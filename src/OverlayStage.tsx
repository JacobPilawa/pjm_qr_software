import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { Competitor, RuntimeState } from "./types";


export type OverlayLayout = { x: number; y: number; width: number; height: number };


export function DetectionLayer({ state }: { state: RuntimeState }) {
  return <svg className="detection-layer" viewBox={`0 0 ${state.width} ${state.height}`} preserveAspectRatio="xMidYMid meet" aria-label="QR detection diagnostics">
    {state.detections.map((detection) => {
      const points = detection.corners.map((point) => point.join(",")).join(" ");
      const first = detection.corners[0] ?? [0, 0];
      const color = detection.focused ? "#43d17d" : "#ef5b55";
      const label = `${detection.focused ? "ACTIVE" : "INACTIVE"} · ${detection.table ? `TABLE ${detection.table}` : "UNKNOWN"} · ${detection.hits}/${state.focus.requiredHits} · ${detection.decoder}`;
      return <g key={`${detection.value}-${points}`}>
        {detection.focused && <polygon points={points} fill="rgba(67,209,125,.09)" stroke="#071015" strokeWidth="14" vectorEffect="non-scaling-stroke"/>}
        <polygon points={points} fill="none" stroke={color} strokeWidth={detection.focused ? 8 : 3} vectorEffect="non-scaling-stroke"/>
        <rect x={first[0]} y={Math.max(0, first[1] - 34)} width={Math.max(280, label.length * 12)} height="31" fill="rgba(7,16,21,.90)"/>
        <text x={first[0] + 9} y={Math.max(22, first[1] - 11)} fill={color} fontSize="20" fontFamily="ui-monospace,monospace" fontWeight="700">{label}</text>
      </g>;
    })}
  </svg>;
}


function fitFont(text: string, available: number, desired: number, minimum: number, maximum: number, factor = .58) {
  const estimated = available / Math.max(1, text.length * factor);
  return Math.max(minimum, Math.min(maximum, desired, estimated));
}


function OverlayGraphic({ state, value, layout }: { state: RuntimeState; value: Competitor; layout: OverlayLayout }) {
  const { x, y, width, height } = layout;
  const metadata = [
    state.overlayShowCity && value.memberNames !== value.name ? value.memberNames : "",
    state.overlayShowUsername ? value.nationality : "",
  ].filter(Boolean) as string[];
  const roundText = `${value.roundName?.toUpperCase()} · TABLE ${value.table}`;
  const name = value.name ?? "";
  const padding = Math.max(20, Math.min(52, width * .0525));
  const accent = Math.max(8, Math.min(20, width * .01625));
  const textX = padding + accent;
  const available = Math.max(100, width - textX - padding);
  const compact = height < 150 || width < 480;
  const roundSize = fitFont(roundText, available, height * .12, 10, 32, .62);
  const nameSize = fitFont(name, available, height * (compact ? .34 : .27), 16, 90);
  const stackedMeta = metadata.length > 1 && width < 560 && !compact;
  const metaLines = stackedMeta ? metadata : metadata.length ? [metadata.join(" · ")] : [];
  const longestMeta = metaLines.reduce((longest, line) => line.length > longest.length ? line : longest, "");
  const metaSize = fitFont(longestMeta, available, height * (stackedMeta ? .10 : .13), 10, 38);

  return <g className="competitor-lower-third" transform={`translate(${x} ${y})`}>
    <rect width={width} height={height} rx={Math.min(10, height * .03)} fill="#0e1138" fillOpacity=".96"/>
    <rect width={accent} height={height} fill="#f1b643"/>
    <text x={textX} y={height * .23} fill="#bfc9dc" fontSize={roundSize} letterSpacing="2" fontFamily="PJM Poppins, Poppins, sans-serif">{roundText}</text>
    <text x={textX} y={height * (compact ? .66 : .56)} fill="#ffffff" fontSize={nameSize} fontWeight="700" fontFamily="PJM Poppins, Poppins, sans-serif">{name}</text>
    {!compact && metaLines.slice(0, 2).map((line, index) => <text key={line} x={textX} y={height * (stackedMeta ? .77 + index * .13 : .82)} fill="#d9e1ed" fontSize={metaSize} fontFamily="PJM Poppins, Poppins, sans-serif">{line}</text>)}
  </g>;
}


function stateLayout(state: RuntimeState): OverlayLayout {
  return { x: state.overlayX, y: state.overlayY, width: state.overlayWidth, height: state.overlayHeight };
}


export function CompetitorOverlay({ state, preview = false }: { state: RuntimeState; preview?: boolean }) {
  const value = state.onAir;
  return <svg className={`competitor-overlay-svg ${preview ? "preview" : ""}`} viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid meet" aria-label="On-air competitor graphic">
    {value?.ok && <OverlayGraphic state={state} value={value} layout={stateLayout(state)}/>}
  </svg>;
}


type DragState = { mode: "move" | "resize"; pointerId: number; startX: number; startY: number; layout: OverlayLayout };


export function EditableCompetitorOverlay({ state, onLayoutCommit }: { state: RuntimeState; onLayoutCommit: (layout: OverlayLayout) => void }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const draftRef = useRef(stateLayout(state));
  const [draft, setDraft] = useState(draftRef.current);
  const [selected, setSelected] = useState(false);

  const updateDraft = (layout: OverlayLayout) => {
    draftRef.current = layout;
    setDraft(layout);
  };

  useEffect(() => {
    if (!dragRef.current) updateDraft(stateLayout(state));
  }, [state.overlayX, state.overlayY, state.overlayWidth, state.overlayHeight]);

  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (svgRef.current && !svgRef.current.contains(event.target as Node)) setSelected(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);

  useEffect(() => {
    if (!state.onAir?.ok) setSelected(false);
  }, [state.onAir?.ok]);

  const point = (event: ReactPointerEvent<SVGElement>) => {
    const svg = svgRef.current;
    const matrix = svg?.getScreenCTM();
    if (!svg || !matrix) return { x: 0, y: 0 };
    const value = svg.createSVGPoint();
    value.x = event.clientX;
    value.y = event.clientY;
    return value.matrixTransform(matrix.inverse());
  };

  const start = (mode: "move" | "resize", event: ReactPointerEvent<SVGElement>) => {
    if (!state.onAir?.ok) return;
    event.preventDefault();
    event.stopPropagation();
    setSelected(true);
    const cursor = point(event);
    dragRef.current = { mode, pointerId: event.pointerId, startX: cursor.x, startY: cursor.y, layout: draftRef.current };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const move = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const cursor = point(event);
    const dx = cursor.x - drag.startX;
    const dy = cursor.y - drag.startY;
    if (drag.mode === "move") {
      updateDraft({
        ...drag.layout,
        x: Math.max(0, Math.min(1920 - drag.layout.width, drag.layout.x + dx)),
        y: Math.max(0, Math.min(1080 - drag.layout.height, drag.layout.y + dy)),
      });
    } else {
      updateDraft({
        ...drag.layout,
        width: Math.max(260, Math.min(1600, 1920 - drag.layout.x, drag.layout.width + dx)),
        height: Math.max(100, Math.min(600, 1080 - drag.layout.y, drag.layout.height + dy)),
      });
    }
  };

  const finish = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!dragRef.current || dragRef.current.pointerId !== event.pointerId) return;
    dragRef.current = null;
    onLayoutCommit(draftRef.current);
  };

  const value = state.onAir;
  const handle = Math.max(30, Math.min(48, draft.height * .18));
  return <svg ref={svgRef} className={`competitor-overlay-svg preview editable-overlay ${selected ? "selected" : ""}`} viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid meet" aria-label="Editable on-air competitor graphic" onPointerDown={(event) => { if (event.target === event.currentTarget) setSelected(false); }} onPointerMove={move} onPointerUp={finish} onPointerCancel={finish}>
    {value?.ok && <>
      <OverlayGraphic state={state} value={value} layout={draft}/>
      <rect className="overlay-move-target" x={draft.x} y={draft.y} width={draft.width} height={draft.height} onPointerDown={(event) => start("move", event)}/>
      {selected && <>
        <rect className="overlay-edit-outline" x={draft.x} y={draft.y} width={draft.width} height={draft.height}/>
        <g className="overlay-resize-target" transform={`translate(${draft.x + draft.width - handle} ${draft.y + draft.height - handle})`} onPointerDown={(event) => start("resize", event)}>
          <rect width={handle} height={handle}/>
          <path d={`M ${handle * .28} ${handle * .78} L ${handle * .78} ${handle * .28} M ${handle * .52} ${handle * .78} L ${handle * .78} ${handle * .52}`}/>
        </g>
        <g className="overlay-edit-label" transform={`translate(${draft.x} ${Math.max(0, draft.y - 28)})`}>
          <rect width="238" height="25" rx="3"/>
          <text x="9" y="17">DRAG TO MOVE · CORNER TO RESIZE</text>
        </g>
      </>}
    </>}
  </svg>;
}
