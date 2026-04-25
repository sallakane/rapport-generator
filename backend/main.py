import asyncio
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from extractor import extract_structure
from filter import filter_document

load_dotenv()

APP_USER = os.getenv('APP_USER', 'admin')
APP_PASSWORD = os.getenv('APP_PASSWORD', 'changeme')
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-prod')
SESSION_MAX_AGE = 3600 * 8  # 8 heures

TMP_DIR = Path(__file__).parent / 'tmp'
TMP_DIR.mkdir(exist_ok=True)

FRONTEND_DIR = Path(__file__).parent.parent / 'frontend'

app = FastAPI(docs_url=None, redoc_url=None)  # pas de swagger public
serializer = URLSafeTimedSerializer(SECRET_KEY)


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


# ── Nettoyage automatique des sessions tmp (toutes les 10 min) ───────────────

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


# ── Routes document ───────────────────────────────────────────────────────────

@app.post('/api/upload')
async def upload(
    file: UploadFile = File(...),
    user: str = Depends(require_auth),
):
    if not file.filename.endswith('.docx'):
        raise HTTPException(status_code=400, detail='Seuls les fichiers .docx sont acceptés')

    session_id = str(uuid.uuid4())
    session_dir = TMP_DIR / session_id
    session_dir.mkdir()

    docx_path = session_dir / 'original.docx'
    content = await file.read()
    docx_path.write_bytes(content)

    try:
        structure = extract_structure(str(docx_path))
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f'Impossible de lire le document : {e}')

    return {'session_id': session_id, 'structure': structure}


class GenerateBody(BaseModel):
    session_id: str
    selected_ids: list[str]


@app.post('/api/generate')
def generate(body: GenerateBody, user: str = Depends(require_auth)):
    session_dir = TMP_DIR / body.session_id
    if not session_dir.exists():
        raise HTTPException(status_code=404, detail='Session expirée ou introuvable')

    # Parser les IDs sélectionnés
    selected_chapters: set[int] = set()
    selected_annexes: set[int] = set()

    for sid in body.selected_ids:
        if sid.startswith('ch_'):
            try:
                selected_chapters.add(int(sid.split('_')[1]))
            except ValueError:
                pass
        elif sid.startswith('annex_'):
            try:
                selected_annexes.add(int(sid.split('_')[1]))
            except ValueError:
                pass

    if not selected_chapters and not selected_annexes:
        raise HTTPException(status_code=400, detail='Aucun chapitre ou annexe sélectionné')

    docx_path = str(session_dir / 'original.docx')
    unpack_dir = str(session_dir / 'unpacked')
    output_path = str(session_dir / 'output.docx')

    try:
        filter_document(
            docx_path=docx_path,
            selected_chapters=selected_chapters,
            selected_annexes=selected_annexes,
            unpack_dir=unpack_dir,
            output_path=output_path,
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f'Erreur lors du traitement : {e.stderr}')
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


# ── Frontend statique (monté en dernier) ────────────────────────────────────
app.mount('/', StaticFiles(directory=str(FRONTEND_DIR), html=True), name='frontend')
