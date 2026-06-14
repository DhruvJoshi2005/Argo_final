import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from download_file import run_download
from insetion_in_database import run_all
from chat_logic import main as chat_main
from chat_main_optimised import main as chat_main_optimised, clear_sql_cache


# ===============================
# APP INIT
# ===============================
app = FastAPI(title="ARGO Data Backend")


# ===============================
# CORS CONFIG
# ===============================
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===============================
# SCHEMAS
# ===============================
class ChatRequest(BaseModel):
    question: str


class Timing(BaseModel):
    intent_ms: float
    sql_ms: float
    total_ms: float
    cache_hit: bool


class ChatResponse(BaseModel):
    answer: str
    sql: str | None = None          # 🔥 ADDED
    timing: Timing | None = None
    error: str | None = None


# ===============================
# ROUTES
# ===============================
@app.get("/")
def home():
    return {"message": "Backend is running!"}


@app.post("/refresh_data")
def refresh_data():
    download_result = run_download()
    run_all()
    clear_sql_cache()
    return {
        "status": "success",
        "download_result": download_result,
        "message": "Data refreshed and inserted into the database"
    }


# ===============================
# UNOPTIMISED CHAT
# ===============================
@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
    try:
        result = chat_main(request.question)
        # result = {
        #   "answer": str,
        #   "sql": str,
        #   "timing": {...}
        # }

        return {
            "answer": result["answer"],
            "sql": result.get("sql"),      # 🔥 ADDED
            "timing": result["timing"],
            "error": None
        }

    except Exception as e:
        return {
            "answer": "",
            "sql": None,
            "timing": None,
            "error": str(e)
        }


# ===============================
# OPTIMISED CHAT
# ===============================
@app.post("/chat_optimised", response_model=ChatResponse)
def chat_optimised_endpoint(request: ChatRequest):
    try:
        result = chat_main_optimised(request.question)
        # result = {
        #   "answer": str,
        #   "sql": str,
        #   "timing": {...}
        # }

        return {
            "answer": result["answer"],
            "sql": result.get("sql"),      # 🔥 ADDED
            "timing": result["timing"],
            "error": None
        }

    except Exception as e:
        return {
            "answer": "",
            "sql": None,
            "timing": None,
            "error": str(e)
        }
