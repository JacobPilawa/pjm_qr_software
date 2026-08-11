# PJM QR Operator

Local macOS dashboard that reads table QR codes from a browser camera or NDI
feed, looks up the current competitor, and provides a transparent lower-third
overlay for vMix.

Competitor data can come from the live tournament API, bundled backup CSVs, or
a fully manual label. QR detection boxes appear only in the operator dashboard.

## Setup and run

Requires Python 3.11+ and Node.js 22.13+.

```bash
cd pjm_qr_software
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
./scripts/start_local.sh
```

The startup script opens the dashboard automatically. Press `Ctrl+C` in its
terminal to stop both servers.

- Dashboard: `http://127.0.0.1:5174`
- vMix browser overlay: `http://127.0.0.1:5174/overlay/main`
- Raw SVG: `http://127.0.0.1:5174/api/overlay.svg`

## Important notes

- Live API mode defaults to the Portland Jigsaw Masters competition. Confirm the
  active round before broadcast.
- Backup CSVs are snapshots and may disagree with the live API. Sources are
  selected explicitly and never merged.
- Manual presets are stored in the operator browser, not in project files.
- `data/rosters/sample_competitors.csv` contains the fixed QR-to-table mapping
  and is required even though its filename says `sample`.
- NDI is optional. The NDI runtime is not bundled; install NDI Tools on the Mac
  if NDI input is needed. Browser camera input works without it.
- Use the LAN overlay address shown in the dashboard when vMix is on another
  computer. Both computers must be on the same network.
- Diagnostic QR boxes are operator-only and are never included in the broadcast
  overlay.
