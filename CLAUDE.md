# DOCX Chapter Filter — Contexte projet pour Claude

## Objectif
Application web qui prend en entrée un **modèle figé** (`modele_word_atlantis.docx` à la racine), affiche sa structure hiérarchique sous forme de cases à cocher (Title → Heading1 → Heading2 → Heading3) ainsi que la liste des annexes en parallèle, puis génère un `.docx` ne contenant que les éléments cochés — avec exactement la même mise en forme (header, footer, styles, orientation portrait/paysage) que le modèle.

## Genèse
Le script `filter_chapters.py` (à la racine) est le prototype historique. La logique a été portée puis étendue dans `backend/filter.py` pour gérer :
- la hiérarchie complète Title/Heading1/Heading2/Heading3 (3 niveaux cochables) ;
- la préservation stricte des sectPr inline (pour ne pas casser les pages paysage) ;
- la **renumérotation automatique** des annexes conservées (1, 5, 7 → 1, 2, 3 dans le doc final).

## Stack technique
- **Backend** : FastAPI (Python 3.12) — API REST, traitement fichiers .docx via lxml + scripts skill docx
- **Frontend** : HTML + vanilla JS (pas de framework) — arbre de checkboxes hiérarchique + bloc annexes
- **Reverse proxy** : Caddy (VPS déjà configuré)
- **Skill docx** : `backend/docx_scripts/office/` — scripts `unpack.py` et `pack.py` pour décompresser/recompresser le .docx
- **Dépendances Python** : `fastapi`, `uvicorn`, `lxml`, `defusedxml`, `itsdangerous`, `python-dotenv`

## Architecture du projet

```
rapport-generator/
├── CLAUDE.md                       ← ce fichier
├── modele_word_atlantis.docx       ← MODÈLE FIGÉ — base de tous les traitements
├── backend/
│   ├── main.py                     ← FastAPI (cache du modèle au startup)
│   ├── extractor.py                ← parse(docx_path) + analyze(body) — hiérarchie + annexes
│   ├── filter.py                   ← filtrage XML + sectPr + renum annexes + repack
│   ├── docx_scripts/office/        ← scripts unpack.py / pack.py
│   ├── requirements.txt
│   ├── .env                        ← identifiants (gitignored)
│   └── tmp/                        ← jobs de génération (gitignored, cleanup auto)
├── frontend/
│   ├── index.html                  ← login + vue sélection (plus de vue upload)
│   ├── style.css
│   └── app.js
├── infra/
│   ├── rapport-generator.service   ← service systemd
│   └── Caddyfile.patch             ← Caddyfile du VPS (référence)
├── venv/                           ← virtualenv Python (gitignored)
├── skills/                         ← skill docx (référence)
├── filter_chapters.py              ← prototype original
└── .gitignore
```

## Convention de styles (modèle ATLANTIS actuel)

Le modèle utilise les **styles standards anglais** (et non `Titre`/`Titre1`/`Titreannexes` comme dans la version précédente) :

- `Title`    → section principale (TITRE EN MAJUSCULES) — **non cochable**, regroupement visuel uniquement
- `Heading1` → chapitre — cochable
- `Heading2` → sous-chapitre — cochable
- `Heading3` → sous-sous-chapitre — cochable (profondeur max ; pas de Heading4 dans le modèle)

**Annexes** : aucun style spécifique. Détectées par regex sur le texte du paragraphe :
```
^Annexe\s*n[°º]\s*(\d+)
```
(ex. `Annexe n°5Coupe(s) du/des sondage(s)…` ; le label suit le numéro sans séparateur).

## API Backend — Endpoints

Toutes les routes (sauf `/api/login` et `/api/health`) requièrent un cookie de session valide.

### POST /api/login — `{ username, password }` → `{ ok: true }` (cookie `session` posé)
### POST /api/logout
### GET  /api/me → `{ user }`

### GET /api/structure
Renvoie l'arbre du modèle (mis en cache au startup) :
```json
{
  "sections": [
    {
      "id": "section_2",
      "label": "INTRODUCTION",
      "chapters": [
        {
          "id": "h1_1",
          "label": "Mission confiée",
          "children": [
            { "id": "h2_1", "label": "...", "children": [
              { "id": "h3_1", "label": "..." }
            ]}
          ]
        }
      ]
    }
  ],
  "annexes": [
    { "id": "annex_1", "num": 1, "label": "Extrait de la norme NF P 94-500" }
  ]
}
```

Les sections sans `chapters` (TOC type SOMMAIRE / table des annexes en début de doc) sont incluses dans la réponse mais filtrées côté frontend ; elles sont **toujours conservées** dans le doc généré (Q1 = (a) : on garde le sommaire figé, Word le rafraîchira à l'ouverture si besoin).

### POST /api/generate — `{ chapters: ["h1_1","h2_3",...], annexes: [1, 5, 7] }`
Renvoie le `.docx` filtré en téléchargement direct.
La cohérence parent/enfant (cascade) est garantie côté frontend ; le backend reçoit déjà une liste cohérente.

### GET /api/health → `{ status: "ok" }`

## Logique d'extraction (extractor.py)

`parse(docx_path)` ouvre le zip, parse `document.xml`, appelle `analyze(body)`.
`analyze(body)` retourne un `ParsedDoc` avec :
- `sections` : arbre `Title → Heading1 → Heading2 → Heading3`
- `annexes` : liste plate `[{id, num, label}]`
- `owners[i]` : pour chaque enfant du body, un tuple disant à quel nœud il appartient :
  - `('cover',)` — page de garde (avant le 1er Title)
  - `('section', idx)` — paragraphe Title lui-même
  - `('h1'|'h2'|'h3', n)` — paragraphe d'un chapitre, ou contenu non-titre rattaché au plus profond actif
  - `('annex', num)` — paragraphe d'une annexe (numéro original)

Cette table d'owners est partagée avec `filter.py` pour décider quoi garder/supprimer.

## Logique de filtrage (filter.py)

1. Unpack du `.docx` (skill `office/unpack.py`).
2. Parse `document.xml` (lxml).
3. Recalcule la table `owners` via `extractor.analyze(body)`.
4. Décide pour chaque enfant du body s'il est **conservé** :
   - Page de garde : toujours.
   - Title : conservé si la section a au moins un chapter sélectionné, OU si la section n'a aucun chapter (cas des TOC, toujours préservées).
   - Heading1/2/3 : conservé si son id est dans `selected_chapters`.
   - Annexe : conservée si son numéro est dans `selected_annexes`.
5. **Override sectPr** : un paragraphe portant un `<w:sectPr>` inline qui devait être supprimé est forcé conservé si sa section englobe au moins un paragraphe conservé. Ses runs sont vidés (`_clear_runs_keep_pPr`) pour ne pas réintroduire de texte parasite. Préserve l'orientation paysage.
6. **Renumérotation des annexes** : pour chaque paragraphe d'annexe conservé, on remappe `Annexe n°<old>` → `Annexe n°<new>` selon l'ordre des annexes conservées (1, 2, 3…). Tentative de remplacement dans un `<w:t>` unique d'abord, fallback cross-run (concatène, remplace, remet tout dans le 1er run).
7. Suppression des enfants marqués (en ordre décroissant pour ne pas perturber les indices).
8. `_fix_rels` neutralise les `Target="file:///..."` Windows et `Target="about:blank"` (rejetés par le validateur du skill `pack.py`).
9. Réécrit `document.xml` et repack via `office/pack.py`.

**Limitation v1 — assumée** : les références textuelles "voir annexe n°5" dans le corps du doc ne sont **pas** remappées (Q2 = (a)). Si l'annexe 5 devient l'annexe 2, le texte continuera de pointer vers "n°5". À éventuellement traiter en v2.

## Cache du modèle
Le modèle est parsé **une seule fois** au startup (`@app.on_event('startup')`) et la structure exposée par `/api/structure` est servie depuis `_MODEL_STRUCTURE` en mémoire. À chaque appel `/api/generate`, le filtrage repart d'un unpack frais dans un dossier `backend/tmp/<job_uuid>/`.

## Nettoyage des fichiers temporaires
- Chaque génération crée `backend/tmp/<job_id>/unpacked/` + `<job_id>/output.docx`
- Boucle asyncio toutes les 10 min : supprime les dossiers de plus de 30 min

## Authentification

L'application est un intranet privé — pas de référencement Google, accès restreint.

### Stratégie retenue : session token simple
- Page de login (vanilla JS) avec formulaire user/password
- Identifiants stockés dans `backend/.env` (`APP_USER`, `APP_PASSWORD`) — jamais dans le code
- À la connexion réussie : cookie `session` signé via `itsdangerous` (`URLSafeTimedSerializer`), httpOnly, samesite=lax, max-age 8 h
- Toutes les routes API vérifient le cookie via la dépendance `require_auth`, retournent 401 sinon
- Le frontend redirige vers la vue login si 401 sur `/api/me`

### No-index (ne pas apparaître sur Google)
Header HTTP dans Caddy :
```
header X-Robots-Tag "noindex, nofollow"
```
Plus `<meta name="robots" content="noindex, nofollow" />` côté HTML.

---

## Déploiement sur le VPS

Le VPS utilise **Caddy** comme reverse proxy. Le projet tourne sur le domaine **ag-rapport-generator.fr**, port interne **8001**.

Cohabite avec `sunu-cagnotte` sur le même VPS (port 8080).

### Premier déploiement (une seule fois)

```bash
# 1. Créer le virtualenv et installer les dépendances
python3 -m venv /var/www/rapport-generator/venv
/var/www/rapport-generator/venv/bin/pip install -r backend/requirements.txt

# 2. Créer le .env (ne jamais committer)
cp backend/.env.example backend/.env   # puis éditer avec les vrais identifiants

# 3. Installer le service systemd
sudo cp infra/rapport-generator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable rapport-generator
sudo systemctl start rapport-generator
```

### Mise à jour du code

```bash
git pull
sudo systemctl restart rapport-generator
```

### Service systemd

- Fichier : `infra/rapport-generator.service` (installé dans `/etc/systemd/system/`)
- Uvicorn démarre sur `127.0.0.1:8001` avec 2 workers
- `EnvironmentFile` pointe vers `backend/.env`
- Commandes utiles :
  ```bash
  sudo systemctl status rapport-generator
  sudo systemctl restart rapport-generator
  sudo journalctl -u rapport-generator -f
  ```

### Configuration Caddy

```
ag-rapport-generator.fr {
    header X-Robots-Tag "noindex, nofollow"
    reverse_proxy localhost:8001
}
```

Après toute modification du Caddyfile :
```bash
sudo systemctl reload caddy
```

## Points de vigilance
- Le modèle pèse ~19 Mo (essentiellement des images). Le doc généré reste de cet ordre tant qu'on garde des chapitres avec images.
- Le skill `pack.py` valide les rels et rejette `file:///` et `about:blank` — `_fix_rels` les neutralise systématiquement.
- La détection des styles est calée sur `Title`/`Heading1`/`Heading2`/`Heading3`. Si on remplace le modèle par un doc utilisant `Titre`/`Titre1`, il faudra ré-étendre `extractor.py`.
- Une seule zone paysage dans le modèle actuel (mini-section "Légende EM…" dans Synthèse pressiométrique). Le mécanisme d'override sectPr est prêt si d'autres apparaissent.
- Ne jamais committer les fichiers dans `backend/tmp/`.

## Ordre de développement
1. [x] Prototype logique métier (`filter_chapters.py`)
2. [x] `backend/extractor.py` — extraction hiérarchique + annexes par regex
3. [x] `backend/filter.py` — filtrage + sectPr + renumérotation annexes
4. [x] `backend/main.py` — FastAPI, cache modèle au startup, plus de /api/upload
5. [x] `frontend/index.html` + `app.js` + `style.css` — arbre + bloc annexes + cascade
6. [ ] Tests manuels en local (uvicorn + Word desktop)
7. [x] Déploiement VPS + configuration Caddy (`ag-rapport-generator.fr`)
