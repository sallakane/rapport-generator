# DOCX Chapter Filter — Contexte projet pour Claude

## Objectif
Application web permettant à un utilisateur d'uploader un fichier `.docx` (modèle de rapport géotechnique ATLANTIS), de sélectionner les chapitres et annexes à conserver via des cases à cocher, puis de télécharger un nouveau `.docx` avec exactement la même mise en forme (header, footer, styles) mais uniquement les sections choisies.

## Genèse
Le script `filter_chapters.py` (à la racine) est le prototype fonctionnel de la logique métier. Il a été utilisé pour filtrer `Modele_Word_ATLANTIS.docx` → `Modele_Word_ATLANTIS_filtered.docx`. C'est la base du backend.

## Stack technique
- **Backend** : FastAPI (Python 3.12) — API REST, traitement fichiers .docx via lxml + scripts skill docx
- **Frontend** : HTML + vanilla JS (pas de framework) — upload, liste de checkboxes, téléchargement
- **Reverse proxy** : Caddy (VPS déjà configuré)
- **Skill docx** : `skills/skills/docx/scripts/` — scripts `unpack.py` et `pack.py` pour décompresser/recompresser le .docx
- **Dépendances Python** : `fastapi`, `uvicorn`, `lxml`, `defusedxml`, `python-multipart`

## Architecture du projet

```
rapport-generator/
├── CLAUDE.md                      ← ce fichier
├── backend/
│   ├── main.py                    ← FastAPI app (endpoints)
│   ├── extractor.py               ← extraction chapitres/annexes depuis .docx
│   ├── filter.py                  ← filtrage XML + repack (issu de filter_chapters.py)
│   ├── requirements.txt
│   └── tmp/                       ← stockage temporaire (gitignored)
├── frontend/
│   ├── index.html                 ← interface unique (upload → checkboxes → download)
│   ├── style.css
│   └── app.js
├── skills/                        ← skill docx (déjà présent, ne pas modifier)
├── filter_chapters.py             ← prototype original (référence)
└── .gitignore
```

## API Backend — Endpoints

### POST /upload
- Reçoit : fichier `.docx` (multipart/form-data)
- Traitement : décompresse le .docx, extrait tous les titres Titre/Titre1/Titreannexes
- Retourne :
```json
{
  "session_id": "uuid",
  "sections": [
    { "id": "section_0", "type": "section", "label": "INTRODUCTION" },
    { "id": "ch_1", "type": "chapter", "num": 1, "label": "Mission confiée", "section": "INTRODUCTION" },
    { "id": "annex_1", "type": "annex", "num": 1, "label": "Annexe n°1 — NF P 94-500" }
  ]
}
```

### POST /generate
- Reçoit :
```json
{ "session_id": "uuid", "selected_ids": ["ch_1", "ch_2", "annex_1", ...] }
```
- Traitement : filtre le .docx selon la sélection, repack
- Retourne : fichier `.docx` en téléchargement direct

### GET /health
- Retourne `{ "status": "ok" }` — utilisé par Caddy/monitoring

## Logique d'extraction (extractor.py)

Styles reconnus :
- `Titre` → section principale (non coché, toujours conservé si au moins un enfant coché)
- `Titre1` → chapitre (coché individuellement)
- `Titre2`, `Titre3` → sous-chapitres (toujours conservés avec leur chapitre parent)
- `Titreannexes` → annexe (cochée individuellement)

Numérotation automatique des Titre1 de 1 à N (ordre d'apparition dans le body).

## Logique de filtrage (filter.py)

Identique à `filter_chapters.py` :
1. Lire `document.xml` avec lxml (préserve les namespaces)
2. Numéroter les Titre1 (chapitres)
3. Pour chaque enfant du body : supprimer si appartient à un chapitre non sélectionné
4. Conserver les Titre-sections même si certains de leurs chapitres sont supprimés
5. Réécrire `document.xml` avec lxml
6. Repack via `skills/skills/docx/scripts/office/pack.py`

## Nettoyage des fichiers temporaires
- Chaque session crée un dossier `backend/tmp/<session_id>/`
- Nettoyage automatique après 30 minutes (via `asyncio` background task)

## Authentification

L'application est un intranet privé — pas de référencement Google, accès restreint.

### Stratégie retenue : session token simple
- Page `/login` avec formulaire user/password
- Identifiants stockés dans `.env` (`APP_USER`, `APP_PASSWORD`) — jamais dans le code
- À la connexion réussie : cookie de session signé (via `itsdangerous` ou `fastapi-sessions`)
- Toutes les routes API vérifient le cookie, retournent 401 sinon
- Le frontend redirige vers `/login` si 401

### No-index (ne pas apparaître sur Google)
Ajout d'un header HTTP dans Caddy :
```
header X-Robots-Tag "noindex, nofollow"
```

### Dépendances supplémentaires pour l'auth
```
itsdangerous==2.2.0   # signature des cookies de session
python-dotenv==1.0.0  # lecture du .env
```

---

## Déploiement sur le VPS

Le VPS utilise **Caddy** comme reverse proxy, déjà configuré.

### Lancement du backend
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

### Configuration Caddy (à ajouter dans Caddyfile)
```
votre-domaine.com {
    reverse_proxy 127.0.0.1:8000
}
```

### Lancement en production (systemd ou screen)
```bash
screen -S docx-filter
uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2
```

## Points de vigilance
- Les fichiers .docx uploadés peuvent être lourds (le modèle ATLANTIS fait 18 Mo)
- Le skill `pack.py` nécessite `lxml` et `defusedxml` installés
- La détection des styles est calée sur les conventions ATLANTIS (`Titre`, `Titre1`, `Titreannexes`). Pour des documents tiers, ajouter une détection fallback sur `Heading1`, `Heading2`, etc.
- Ne jamais committer les fichiers dans `backend/tmp/`

## Ordre de développement
1. [x] Prototype logique métier (`filter_chapters.py`)
2. [ ] `backend/extractor.py` — extraction des chapitres
3. [ ] `backend/filter.py` — filtrage + repack (portage de filter_chapters.py)
4. [ ] `backend/main.py` — FastAPI avec les 3 endpoints
5. [ ] `frontend/index.html` + `app.js` + `style.css`
6. [ ] Tests manuels en local
7. [ ] Déploiement VPS + configuration Caddy
