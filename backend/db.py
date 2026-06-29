"""Couche SQLite : comptes utilisateurs + journal des téléchargements.

Stockage léger (module stdlib `sqlite3`, aucune dépendance externe).
- table `users`  : login, hash de mot de passe, rôle (admin / generateur).
- table `events` : un enregistrement par génération réussie (qui, quand, type de projet).

Les timestamps sont stockés en **UTC** (ISO) et reconvertis en Europe/Paris
au moment de l'affichage (le VPS tourne en UTC).
"""
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path(os.getenv('APP_DB_PATH') or (Path(__file__).parent / 'app.db'))
PARIS = ZoneInfo('Europe/Paris')

_PBKDF2_ITER = 200_000


# ── Connexion ────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


# ── Hash de mot de passe (pbkdf2_hmac, stdlib) ───────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, _PBKDF2_ITER)
    return f'pbkdf2_sha256${_PBKDF2_ITER}${salt.hex()}${dk.hex()}'


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_s, salt_hex, hash_hex = stored.split('$')
        if algo != 'pbkdf2_sha256':
            return False
        dk = hashlib.pbkdf2_hmac('sha256', password.encode(),
                                 bytes.fromhex(salt_hex), int(iter_s))
        return secrets.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ── Init + seed ──────────────────────────────────────────────────────────────

def init_db(seed_admin_user: str | None, seed_admin_password: str | None) -> None:
    """Crée les tables si besoin et sème le compte admin depuis le .env.

    Le seed n'a lieu que si aucun utilisateur n'existe (premier démarrage).
    Si l'admin existe déjà mais que son mot de passe a changé dans le .env,
    on resynchronise son hash (pratique pour ne pas se verrouiller dehors).
    """
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL CHECK (role IN ('admin', 'generateur')),
                created_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT NOT NULL,
                project_type TEXT NOT NULL,
                n_chapters  INTEGER NOT NULL DEFAULT 0,
                n_annexes   INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
            CREATE INDEX IF NOT EXISTS idx_events_user ON events(username);
            """
        )
        if not seed_admin_user or not seed_admin_password:
            return
        row = conn.execute('SELECT id FROM users WHERE username = ?',
                           (seed_admin_user,)).fetchone()
        now = datetime.now(timezone.utc).isoformat()
        if row is None:
            count = conn.execute('SELECT COUNT(*) AS c FROM users').fetchone()['c']
            # On ne sème l'admin que si la table est vierge (premier démarrage).
            if count == 0:
                conn.execute(
                    'INSERT INTO users (username, password_hash, role, created_at) '
                    'VALUES (?, ?, ?, ?)',
                    (seed_admin_user, hash_password(seed_admin_password), 'admin', now),
                )
        else:
            # Resynchronise le mot de passe admin depuis le .env (source de vérité).
            conn.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                        (hash_password(seed_admin_password), row['id']))


# ── Auth ─────────────────────────────────────────────────────────────────────

def authenticate(username: str, password: str) -> str | None:
    """Retourne le rôle si les identifiants sont valides, sinon None."""
    with _conn() as conn:
        row = conn.execute(
            'SELECT password_hash, role FROM users WHERE username = ?',
            (username,)).fetchone()
    if row and verify_password(password, row['password_hash']):
        return row['role']
    return None


def get_role(username: str) -> str | None:
    """Rôle courant de l'utilisateur (relu en base à chaque requête)."""
    with _conn() as conn:
        row = conn.execute('SELECT role FROM users WHERE username = ?',
                          (username,)).fetchone()
    return row['role'] if row else None


# ── CRUD utilisateurs ────────────────────────────────────────────────────────

class UserError(Exception):
    """Erreur métier de gestion des utilisateurs (message affichable)."""


def list_users() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            'SELECT id, username, role, created_at FROM users ORDER BY role, username'
        ).fetchall()
    return [
        {
            'id': r['id'],
            'username': r['username'],
            'role': r['role'],
            'created_at': _to_paris(r['created_at']),
        }
        for r in rows
    ]


def get_user(user_id: int) -> dict | None:
    with _conn() as conn:
        r = conn.execute('SELECT id, username, role FROM users WHERE id = ?',
                        (user_id,)).fetchone()
    return dict(r) if r else None


def create_user(username: str, password: str, role: str = 'generateur') -> dict:
    username = (username or '').strip()
    if not username:
        raise UserError('Identifiant requis.')
    if not password:
        raise UserError('Mot de passe requis.')
    if role not in ('admin', 'generateur'):
        raise UserError('Rôle invalide.')
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _conn() as conn:
            cur = conn.execute(
                'INSERT INTO users (username, password_hash, role, created_at) '
                'VALUES (?, ?, ?, ?)',
                (username, hash_password(password), role, now))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        raise UserError(f'L\'identifiant « {username} » existe déjà.')
    return {'id': uid, 'username': username, 'role': role}


def rename_user(user_id: int, new_username: str) -> None:
    new_username = (new_username or '').strip()
    if not new_username:
        raise UserError('Identifiant requis.')
    try:
        with _conn() as conn:
            cur = conn.execute('UPDATE users SET username = ? WHERE id = ?',
                              (new_username, user_id))
            if cur.rowcount == 0:
                raise UserError('Utilisateur introuvable.')
    except sqlite3.IntegrityError:
        raise UserError(f'L\'identifiant « {new_username} » existe déjà.')


def set_password(user_id: int, password: str) -> None:
    if not password:
        raise UserError('Mot de passe requis.')
    with _conn() as conn:
        cur = conn.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                          (hash_password(password), user_id))
        if cur.rowcount == 0:
            raise UserError('Utilisateur introuvable.')


def delete_user(user_id: int) -> None:
    with _conn() as conn:
        cur = conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        if cur.rowcount == 0:
            raise UserError('Utilisateur introuvable.')


# ── Journal des téléchargements ──────────────────────────────────────────────

def log_event(username: str, project_type: str, n_chapters: int, n_annexes: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            'INSERT INTO events (username, project_type, n_chapters, n_annexes, created_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (username, project_type or 'Personnalisé', n_chapters, n_annexes, now))


def _to_paris(iso_utc: str) -> str:
    """ISO UTC → 'YYYY-MM-DD HH:MM' en heure de Paris."""
    try:
        dt = datetime.fromisoformat(iso_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(PARIS).strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return iso_utc


def fetch_events(limit: int = 500) -> list[dict]:
    """Lignes détaillées (les plus récentes d'abord), pour le tableau admin."""
    with _conn() as conn:
        rows = conn.execute(
            'SELECT username, project_type, n_chapters, n_annexes, created_at '
            'FROM events ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
    out = []
    for r in rows:
        dt = datetime.fromisoformat(r['created_at'])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(PARIS)
        out.append({
            'date': local.strftime('%Y-%m-%d'),
            'time': local.strftime('%H:%M'),
            'username': r['username'],
            'project_type': r['project_type'],
            'n_chapters': r['n_chapters'],
            'n_annexes': r['n_annexes'],
        })
    return out


def stats() -> dict:
    """Agrégats pour les graphes admin (calculés côté Python en heure de Paris)."""
    with _conn() as conn:
        rows = conn.execute(
            'SELECT username, project_type, created_at FROM events').fetchall()

    by_user: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_day: dict[str, int] = {}
    for r in rows:
        by_user[r['username']] = by_user.get(r['username'], 0) + 1
        by_type[r['project_type']] = by_type.get(r['project_type'], 0) + 1
        dt = datetime.fromisoformat(r['created_at'])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        day = dt.astimezone(PARIS).strftime('%Y-%m-%d')
        by_day[day] = by_day.get(day, 0) + 1

    return {
        'total': len(rows),
        'by_user': [{'label': k, 'count': v}
                    for k, v in sorted(by_user.items(), key=lambda x: -x[1])],
        'by_type': [{'label': k, 'count': v}
                    for k, v in sorted(by_type.items(), key=lambda x: -x[1])],
        'by_day': [{'label': k, 'count': by_day[k]} for k in sorted(by_day)],
    }
