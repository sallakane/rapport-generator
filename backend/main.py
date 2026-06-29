import asyncio
import json
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

import db
from extractor import parse, to_api_structure
from filter import filter_document

load_dotenv()

APP_USER = os.getenv('APP_USER', 'admin')
APP_PASSWORD = os.getenv('APP_PASSWORD', 'changeme')
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-prod')
SESSION_MAX_AGE = 3600 * 8

ROOT = Path(__file__).parent.parent
MODEL_PATH = ROOT / 'modele_word_atlantis.docx'
PRESETS_PATH = Path(__file__).parent / 'project_presets.json'
TMP_DIR = Path(__file__).parent / 'tmp'
TMP_DIR.mkdir(exist_ok=True)
FRONTEND_DIR = ROOT / 'frontend'

app = FastAPI(docs_url=None, redoc_url=None)
serializer = URLSafeTimedSerializer(SECRET_KEY)

# Cache du modèle figé : parsé une fois au startup, exposé via /api/structure
_MODEL_STRUCTURE: dict | None = None
# Presets « type de projet » → chapitres pré-cochés, chargés au startup
_PRESETS: list[dict] = []


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
    # On vérifie que le compte existe toujours (révocation possible côté admin).
    if db.get_role(user) is None:
        raise HTTPException(status_code=401, detail='Compte introuvable')
    return user


def require_admin(request: Request) -> str:
    user = require_auth(request)
    if db.get_role(user) != 'admin':
        raise HTTPException(status_code=403, detail='Accès réservé à l\'administrateur')
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
    global _MODEL_STRUCTURE, _PRESETS
    if not MODEL_PATH.exists():
        raise RuntimeError(f'Modèle introuvable : {MODEL_PATH}')
    db.init_db(APP_USER, APP_PASSWORD)
    parsed = parse(str(MODEL_PATH))
    _MODEL_STRUCTURE = to_api_structure(parsed)
    if PRESETS_PATH.exists():
        _PRESETS = json.loads(PRESETS_PATH.read_text(encoding='utf-8')).get('presets', [])
    asyncio.create_task(_cleanup_loop())


# ── Routes auth ──────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str
    password: str


@app.post('/api/login')
def login(body: LoginBody, response: Response):
    role = db.authenticate(body.username, body.password)
    if role is None:
        raise HTTPException(status_code=401, detail='Identifiants incorrects')
    _set_cookie(response, body.username)
    return {'ok': True, 'role': role}


@app.post('/api/logout')
def logout(response: Response):
    response.delete_cookie('session')
    return {'ok': True}


@app.get('/api/me')
def me(user: str = Depends(require_auth)):
    return {'user': user, 'role': db.get_role(user)}


# ── Routes document ──────────────────────────────────────────────────────────

@app.get('/api/structure')
def structure(user: str = Depends(require_auth)):
    return _MODEL_STRUCTURE


@app.get('/api/presets')
def presets(user: str = Depends(require_auth)):
    """Types de projet prédéfinis → liste d'ids de chapitres à pré-cocher."""
    return {'presets': _PRESETS}


class GenerateBody(BaseModel):
    chapters: list[str]   # ids cochés (h1_*, h2_*, h3_*) — cohérence garantie par le frontend
    annexes: list[int]    # numéros originaux d'annexes
    project_type: str | None = None  # label du preset choisi, sinon « Personnalisé »


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

    # Traçage : on n'enregistre que les générations réussies.
    project_type = (body.project_type or '').strip() or 'Personnalisé'
    try:
        db.log_event(user, project_type, len(selected_chapters), len(selected_annexes))
    except Exception:
        pass  # le téléchargement ne doit jamais échouer à cause du journal

    return FileResponse(
        output_path,
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        filename='Rapport ATLANTIS R-26XXXXXX P1 V1 Mission GX-XXX VILLE (DP).docx',
    )


# ── Routes admin (réservées au rôle admin) ───────────────────────────────────

class CreateUserBody(BaseModel):
    username: str
    password: str


class RenameUserBody(BaseModel):
    username: str


class PasswordBody(BaseModel):
    password: str


@app.get('/api/users')
def users_list(admin: str = Depends(require_admin)):
    return {'users': db.list_users()}


@app.post('/api/users')
def users_create(body: CreateUserBody, admin: str = Depends(require_admin)):
    try:
        # Les comptes créés via l'UI sont toujours des « générateurs ».
        user = db.create_user(body.username, body.password, role='generateur')
    except db.UserError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'ok': True, 'user': user}


@app.patch('/api/users/{user_id}/username')
def users_rename(user_id: int, body: RenameUserBody, admin: str = Depends(require_admin)):
    target = db.get_user(user_id)
    if target and target['username'] == admin:
        raise HTTPException(status_code=400, detail='Vous ne pouvez pas renommer votre propre compte.')
    try:
        db.rename_user(user_id, body.username)
    except db.UserError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'ok': True}


@app.patch('/api/users/{user_id}/password')
def users_password(user_id: int, body: PasswordBody, admin: str = Depends(require_admin)):
    try:
        db.set_password(user_id, body.password)
    except db.UserError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'ok': True}


@app.delete('/api/users/{user_id}')
def users_delete(user_id: int, admin: str = Depends(require_admin)):
    target = db.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail='Utilisateur introuvable.')
    # Garde-fous : pas d'auto-suppression, pas de suppression d'un admin.
    if target['username'] == admin:
        raise HTTPException(status_code=400, detail='Vous ne pouvez pas supprimer votre propre compte.')
    if target['role'] == 'admin':
        raise HTTPException(status_code=400, detail='Impossible de supprimer un compte administrateur.')
    db.delete_user(user_id)
    return {'ok': True}


@app.get('/api/stats')
def stats(admin: str = Depends(require_admin)):
    return db.stats()


@app.get('/api/events')
def events(admin: str = Depends(require_admin)):
    return {'events': db.fetch_events()}


@app.get('/api/health')
def health():
    return {'status': 'ok'}


# ── Frontend statique (monté en dernier) ─────────────────────────────────────
app.mount('/', StaticFiles(directory=str(FRONTEND_DIR), html=True), name='frontend')
