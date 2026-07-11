# Argo Float Data Backend

**Made by Dhruv Joshi**

A natural-language API over INCOIS Argo profiling float data. Users ask questions in plain English; the backend extracts intent, generates SQL, and returns a human-readable answer backed by real oceanographic measurements.

## What it does

- Downloads NetCDF files from the INCOIS Argo data portal (Indian Ocean region)
- Parses and ingests them into PostgreSQL across 6 normalised tables
- Exposes a FastAPI backend with a chat endpoint powered by GPT-4.1-mini intent extraction
- Supports queries by named ocean region, explicit coordinates, depth range, and metric

## Data

| Metric | Column |
|---|---|
| Temperature | `temperature` |
| Salinity | `salinity` |
| Pressure | `pressure` |
| Dissolved Oxygen | `doxy` |
| Chlorophyll-a | `chla` |
| Backscatter | `bbp700` |
| pH | `ph_in_situ_total` |

Current data: **262 floats · 33,861 cycles · 16.8 M measurements** (Jan 2026 – present)

## Running locally

```bash
# 1. Create a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt

# 2. Fill in .env
cp .env.example .env

# 3. Start the API
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

## Running with Docker

```bash
docker compose up --build
```

API at `http://localhost:8000`. PostgreSQL is created automatically.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Health ping |
| GET | `/health` | DB stats (floats, cycles, measurements, last observation) |
| POST | `/chat_optimised` | Natural-language query → answer + SQL |
| GET | `/float/{id}/track` | Float trajectory (lat/lon per cycle) |
| GET | `/profile/{id}/{cycle}` | Vertical profile (T/S/O2/Chl vs pressure) |
| POST | `/export` | Same as chat but returns a CSV download |

### Example

```bash
curl -X POST http://localhost:8000/chat_optimised \
  -H "Content-Type: application/json" \
  -d '{"question": "what is the average temperature in the arabian sea"}'
```

```json
{
  "answer": "Temperature averages 11.36 (range -2.08 to 32.71) across 7,562,484 observations...",
  "sql": "SELECT AVG(temperature) ...",
  "timing": { "intent_ms": 580, "sql_ms": 720, "total_ms": 1300, "cache_hit": false }
}
```

## Running tests

```bash
python -m pytest tests/ -v
```

107 tests, no external dependencies (no DB or LLM calls needed).

## Project structure

```
├── main.py                  # FastAPI app + all endpoints
├── chat_main_optimised.py   # NL -> intent -> SQL -> answer pipeline
├── download_file.py         # INCOIS data downloader
├── insetion_in_database.py  # Ingestion orchestrator
├── ingestion_logic/
│   ├── meta_ingestion.py
│   ├── tech_ingestion.py
│   ├── rtraj_ingestion.py
│   ├── prof_ingestion.py
│   ├── sprof_ingestion.py
│   └── flat_table_ingestion.py
└── tests/
    ├── test_ingestion.py
    └── test_chat.py
```
