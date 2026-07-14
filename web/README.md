# Pendulastic Web Stack

## Backend (FastAPI)

```powershell
# From repo root — uses the project .venv
.venv\Scripts\python.exe -m uvicorn web.api.app:app --reload --port 8000
```

## Frontend (Vite + React)

```powershell
cd web\frontend
npm install      # first time only
npm run dev      # → http://localhost:5173
```

Vite proxies `/api/*` → `http://localhost:8000` so both servers must be running.

## Directory structure

```
web/
├── api/
│   ├── app.py                   FastAPI entry point
│   ├── store.py                 In-memory trial/participant dicts
│   ├── requirements.txt
│   ├── models/
│   │   ├── trial.py             Pydantic trial + analysis models
│   │   └── participant.py       Pydantic participant model
│   ├── routers/
│   │   ├── trials.py            POST record, adjust-markers, approve; GET analysis, export-csv
│   │   └── participants.py      CRUD
│   └── services/
│       ├── pipeline_bridge.py   Async subprocess → pendulastic_pipeline.py
│       ├── pt_score_bridge.py   Direct import → pendulastic_pt_score.py
│       └── marker_store.py      Thread-safe tracking_selections.json wrapper
└── frontend/
    ├── src/
    │   ├── types/index.ts       TypeScript interfaces
    │   ├── store/index.ts       Zustand + immer (protocol gate)
    │   ├── api/client.ts        Typed fetch wrapper
    │   ├── App.tsx              Root component / screen router
    │   ├── components/
    │   │   ├── layout/          Sidebar, TopBar
    │   │   ├── screens/         Dashboard, Participant, Record, Review, Analysis
    │   │   └── ui/              FittingApproval, MarkerAdjust, WaveformPlot,
    │   │                        ParameterTable, FidelityBanner
    │   └── index.css            Tailwind directives + component utilities
    ├── package.json
    ├── vite.config.ts           Proxy /api → :8000
    ├── tsconfig.json
    ├── tailwind.config.js
    └── postcss.config.js
```
