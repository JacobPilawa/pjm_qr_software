# PJM QR Operator

Local macOS dashboard that reads table QR codes from a browser camera or NDI
feed, looks up the current competitor, and provides a transparent lower-third
overlay for vMix.

Competitor data can come from the live tournament API, bundled backup CSVs, or
a fully manual label. QR detection boxes appear only in the operator dashboard.

## Setup and run

Requires 64-bit Python 3.11+ and Node.js 22.13+.

### Windows

Install [Python for Windows](https://www.python.org/downloads/windows/) with
**Add Python to PATH** enabled, then install the current
[Node.js LTS](https://nodejs.org/). Double-click `start_windows.bat` in the
project folder.

The first launch creates a private Python environment and installs all missing
dependencies in the same window. It then starts both services and opens the
dashboard automatically. Keep that window open; press `Ctrl+C` to stop the app.
If Windows Firewall asks, allow Python and Node.js on private networks.

### macOS

```bash
cd pjm_qr_software
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
./scripts/start_local.sh
```

The startup script opens the dashboard automatically. Press `Ctrl+C` in its
terminal to stop both services.

### Local addresses

- Dashboard: `http://127.0.0.1:5174`
- vMix browser overlay: `http://127.0.0.1:5174/overlay/main`
- Raw SVG: `http://127.0.0.1:5174/api/overlay.svg`

## Features and core workflow

1. **Choose the video source.** Use **Browser camera / phone** for testing or
   select an available NDI feed for production.
2. **Choose the competitor data.** In **Event Data**, use **Live API** and select
   the active round. **Backup CSV** provides an offline fallback, while
   **Manual** lets the operator enter and save emergency on-air labels.
3. **Detect and confirm a table.** The dashboard decodes the table QR, looks up
   its current assignment, and stabilizes the selected competitor across brief
   missed reads. **Focus Behavior** controls confirmation hits, the hit window,
   dropout hold, and when a larger competing QR may take focus.
4. **Check the result.** **Detected Competitor** shows the current candidate and
   provides overlay on/off, lock, and reset controls. **Diagnostics** can draw
   green and red QR boxes in the operator preview; these never appear on air.
5. **Prepare the graphic.** In **Overlay Content**, drag and resize the
   lower-third and choose whether member names and nationality are included.
6. **Send it to vMix.** In **vMix Outputs**, select the appropriate local or LAN
   address and add the transparent browser-overlay URL as a 1920×1080 browser
   input. The raw SVG URL is also available for polling or troubleshooting.

## Important notes

- Live API mode defaults to the Portland Jigsaw Masters competition. Confirm the
  active round before broadcast.
- Backup CSVs are snapshots and may disagree with the live API. Sources are
  selected explicitly and never merged.
- Manual presets are stored in the operator browser, not in project files.
- `data/rosters/sample_competitors.csv` contains the fixed QR-to-table mapping
  and is required even though its filename says `sample`.
- NDI is optional. The NDI runtime is not bundled; install 64-bit NDI Tools on
  the computer if NDI input is needed. Browser camera input works without it.
- On Windows, `start_windows.bat` is the normal entry point. There is no need to
  open separate backend and frontend terminals.
- Use the LAN overlay address shown in the dashboard when vMix is on another
  computer. Both computers must be on the same network.
- Diagnostic QR boxes are operator-only and are never included in the broadcast
  overlay.
