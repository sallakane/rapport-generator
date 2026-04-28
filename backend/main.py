import asyncio
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from extractor import parse, to_api_structure
from filter import filter_document

load_dotenv()

APP_USER = os.getenv('APP_USER', 'admin')
APP_PASSWORD = os.getenv('APP_PASSWORD', 'changeme')
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-prod')
SESSION_MAX_AGE = 3600 * 8

ROOT = Path(__file__).parent.parent
MODEL_PATH = ROOT / 'modele_word_atlantis.docx'
TMP_DIR = Path(__file__).parent / 'tmp'
TMP_DIR.mkdir(exist_ok=True)
FRONTEND_DIR = ROOT / 'frontend'

app = FastAPI(docs_url=None, redoc_url=None)
serializer = URLSafeTimedSerializer(SECRET_KEY)

# Cache du modèle figé : parsé une fois au startup, exposé via /api/structure
_MODEL_STRUCTURE: dict | None = None


# ── Auth ─────────────────────────────────────────────────────────────────────

def _set_cookie(response: Response, user: str):
    token = serializer.dumps(user)
    response.set_cookie(
        'session', token,
        httponly=True, samesite='lax',
        max_age=SESSION_MAX_AGE,
    )


def _get_user(request: Request) -> str | None:
    token = request.cookies.get('session')
    if not token:
        return None
    try:
        return serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def require_auth(request: Request) -> str:
    user = _get_user(request)
    if not user:
        raise HTTPException(status_code=401, detail='Non autorisé')
    return user


# ── Nettoyage automatique des fichiers de génération (toutes les 10 min) ─────

async def _cleanup_loop():
    while True:
        await asyncio.sleep(600)
        cutoff = datetime.now() - timedelta(minutes=30)
        for d in TMP_DIR.iterdir():
            if d.is_dir():
                mtime = datetime.fromtimestamp(d.stat().st_mtime)
                if mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)


@app.on_event('startup')
async def startup():
    global _MODEL_STRUCTURE
    if not MODEL_PATH.exists():
        raise RuntimeError(f'Modèle introuvable : {MODEL_PATH}')
    parsed = parse(str(MODEL_PATH))
    _MODEL_STRUCTURE = to_api_structure(parsed)
    asyncio.create_task(_cleanup_loop())


# ── Routes auth ──────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str
    password: str


@app.post('/api/login')
def login(body: LoginBody, response: Response):
    if body.username != APP_USER or body.password != APP_PASSWORD:
        raise HTTPException(status_code=401, detail='Identifiants incorrects')
    _set_cookie(response, body.username)
    return {'ok': True}


@app.post('/api/logout')
def logout(response: Response):
    response.delete_cookie('session')
    return {'ok': True}


@app.get('/api/me')
def me(user: str = Depends(require_auth)):
    return {'user': user}


# ── Routes document ──────────────────────────────────────────────────────────

@app.get('/api/structure')
def structure(user: str = Depends(require_auth)):
    return _MODEL_STRUCTURE


class GenerateBody(BaseModel):
    chapters: list[str]   # ids cochés (h1_*, h2_*, h3_*) — cohérence garantie par le frontend
    annexes: list[int]    # numéros originaux d'annexes


@app.post('/api/generate')
def generate(body: GenerateBody, user: str = Depends(require_auth)):
    selected_chapters = set(body.chapters)
    selected_annexes = set(body.annexes)

    if not selected_chapters and not selected_annexes:
        raise HTTPException(status_code=400, detail='Aucun chapitre ou annexe sélectionné')

    job_id = str(uuid.uuid4())
    job_dir = TMP_DIR / job_id
    job_dir.mkdir()
    unpack_dir = str(job_dir / 'unpacked')
    output_path = str(job_dir / 'output.docx')

    try:
        filter_document(
            docx_path=str(MODEL_PATH),
            selected_chapters=selected_chapters,
            selected_annexes=selected_annexes,
            unpack_dir=unpack_dir,
            output_path=output_path,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b'').decode('utf-8', errors='replace')
        raise HTTPException(status_code=500, detail=f'Erreur traitement : {stderr[:300]}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Erreur inattendue : {e}')

    return FileResponse(
        output_path,
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        filename='rapport_filtre.docx',
    )


@app.get('/api/health')
def health():
    return {'status': 'ok'}


# ── Frontend statique (monté en dernier) ─────────────────────────────────────
app.mount('/', StaticFiles(directory=str(FRONTEND_DIR), html=True), name='frontend')
