#!/usr/bin/env python3
"""
Construit backend/project_presets.json à partir de :
  - exemple-sommaire.docx  (les 3 tables des matières = les 3 types de projet)
  - modele_word_atlantis.docx  (la structure de référence + ses ids h1_/h2_/h3_)

Pourquoi un script et pas un mapping écrit à la main ?
  Les libellés des sommaires de l'exemple NE correspondent PAS proprement à ceux
  du modèle (formes génériques avec « / » et « (s) » côté modèle, hiérarchie
  différente, chapitres absents…). Ce script fait le rapprochement
  automatiquement (normalisation + désambiguïsation par contexte parent) et
  applique une table d'OVERRIDES pour les cas que le matching ne peut pas
  trancher seul.

Les décisions encore en attente de validation par le propriétaire du site sont
balisées « PENDING » ci-dessous et documentées dans docs/PRESETS_MAPPING.md.
Pour mettre à jour le mapping : éditer OVERRIDES_RAW puis relancer :

    venv/bin/python scripts/build_presets.py
"""
import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))
from extractor import parse, to_api_structure  # noqa: E402

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
WNS = f'{{{W}}}'

MODEL_PATH = ROOT / 'modele_word_atlantis.docx'
EXAMPLE_PATH = ROOT / 'exemple-sommaire.docx'
OUTPUT_PATH = ROOT / 'backend' / 'project_presets.json'

# ── Décisions de correspondance (clés = libellé brut du sommaire) ──────────────
# [] = entrée ignorée (pas d'équivalent fiable dans le modèle).
# Les lignes « PENDING » attendent la validation du propriétaire — voir
# docs/PRESETS_MAPPING.md.
OVERRIDES_RAW = {
    "Cadre de l’étude": [],                                    # regroupement du sommaire, ignoré
    "Textes réglementaires": ["h1_2", "h2_1", "h2_2"],         # PENDING
    "Documents communiqués": ["h1_2", "h2_1", "h2_2"],         # PENDING
    "Synthèse des essais en laboratoire": ["h1_13"],
    "Identifications G.T.R.": ["h2_18"],
    "Analyses physico-chimiques": ["h2_21"],
    "Masses volumiques": ["h2_25"],
    "Agressivité des sols vis-à-vis du béton": ["h3_2"],       # PENDING (sols only ; entraîne h2_29 parent)
    "Synthèse des fouilles à la pelle mécanique": ["h1_14"],
    "Plateforme des dallages et des voiries": ["h1_23"],
    "Dimensionnement des inclusions rigides": ["h2_54"],
    "Paramètres de dimensionnement": ["h3_17"],
    "Dallages": ["h1_31"],
    "Terrassement du bassin": ["h1_32"],
    "Dimensionnement des voiries": ["h2_84"],
    "Aléas et risques résiduels": ["h1_45"],
    "Pré-dimensionnement des fondations superficielles": ["h2_53"],
    "Dimensionnent de fondations superficielles": ["h2_53"],   # typo dans l'exemple
    "Estimation des tassements des fondations": ["h3_14"],
    "Contexte de mitoyenneté entre fondations voisines": [],   # PENDING (ignoré par défaut)
}


def norm(t: str) -> str:
    """Normalise un libellé pour le rapprochement (garde les accents)."""
    t = t.lower().replace('’', "'")
    t = re.sub(r'\(s\)|\(x\)|\(micro\)', '', t)   # marqueurs pluriel/optionnels
    t = re.sub(r'\(.*?\)', '', t)                  # autres parenthèses
    t = re.sub(r"[^a-zàâäéèêëîïôöùûüç' ]", ' ', t)
    t = t.replace("'", ' ')
    return re.sub(r'\s+', ' ', t).strip()


def deaccent(t: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', t)
                   if unicodedata.category(c) != 'Mn')


def slug(t: str) -> str:
    return re.sub(r'_+', '_', deaccent(norm(t)).replace(' ', '_'))[:40].strip('_')


def _style(p) -> str:
    pPr = p.find(f'{WNS}pPr')
    st = pPr.find(f'{WNS}pStyle') if pPr is not None else None
    return st.get(f'{WNS}val', '') if st is not None else ''


def _text(p) -> str:
    return ''.join(t.text or '' for t in p.findall(f'.//{WNS}t')).strip()


def _clean_toc(t: str) -> str:
    """Retire la numérotation de tête (1.2.3.) et le numéro de page de fin."""
    return re.sub(r'\d+$', '', re.sub(r'^\d+(\.\d+)*\.?', '', t)).strip()


def build_model_index():
    """Renvoie (label_of, parent_of, norm_index) du modèle."""
    parsed = parse(str(MODEL_PATH))
    s = to_api_structure(parsed)
    label_of, parent_of, index = {}, {}, {}

    def add(idd, lbl):
        label_of[idd] = lbl
        for part in lbl.split('/'):
            index.setdefault(norm(part), []).append(idd)

    for sec in s['sections']:
        for h1 in sec['chapters']:
            add(h1['id'], h1['label'])
            for h2 in h1.get('children', []):
                add(h2['id'], h2['label'])
                parent_of[h2['id']] = h1['id']
                for h3 in h2.get('children', []):
                    add(h3['id'], h3['label'])
                    parent_of[h3['id']] = h2['id']
    return label_of, parent_of, index


def ancestors(idd, parent_of):
    out, x = [], parent_of.get(idd)
    while x:
        out.append(x)
        x = parent_of.get(x)
    return out


def parse_example():
    """Renvoie [{'title', 'entries':[(level, label), …]}] depuis l'exemple."""
    with zipfile.ZipFile(EXAMPLE_PATH) as z:
        body = etree.parse(z.open('word/document.xml')).getroot().find(f'{WNS}body')
    projects = []
    for el in body:
        if etree.QName(el.tag).localname != 'p':
            continue
        st, tx = _style(el), _text(el)
        if st in ('TM1', 'TM2', 'TM3'):
            projects[-1]['entries'].append((int(st[2]), _clean_toc(tx)))
        elif tx and st == '':                       # titre = un type de projet
            projects.append({'title': tx.replace('’', "'"), 'entries': []})
    return projects


def disambiguate(cands, ctx, level, parent_of):
    """Choisit parmi plusieurs candidats grâce au contexte parent."""
    if len(cands) <= 1:
        return cands, ''
    direct = [c for c in cands if parent_of.get(c) == ctx]
    if len(direct) == 1:
        return direct, ''
    anc = [c for c in cands if ctx in ancestors(c, parent_of)]
    if len(anc) == 1:
        return anc, ''
    if level == 1:
        tops = [c for c in cands if c.startswith('h1_')]
        if len(tops) == 1:
            return tops, ''
    return cands, f'AMBIGU {cands}'


def main():
    label_of, parent_of, index = build_model_index()
    overrides = {norm(k): v for k, v in OVERRIDES_RAW.items()}
    projects = parse_example()

    presets, warnings = [], []
    for pr in projects:
        chosen, parent_match = [], {}
        for level, label in pr['entries']:
            n = norm(label)
            is_override = n in overrides
            ids = overrides[n] if is_override else index.get(n, [])
            ctx = parent_match.get(level - 1)
            if not is_override:                     # overrides = décision déjà figée
                ids, warn = disambiguate(ids, ctx, level, parent_of)
                if warn:
                    warnings.append((pr['title'], level, label, warn))
                if not ids:
                    warnings.append((pr['title'], level, label, 'AUCUN MATCH'))
            chosen.extend(ids)
            if ids:
                parent_match[level] = ids[0]
            for deeper in [d for d in parent_match if d > level]:
                parent_match.pop(deeper)

        # Expansion des ancêtres (cohérence parent/enfant) + dédoublonnage
        full = []
        for idd in chosen:
            for a in reversed(ancestors(idd, parent_of)):
                if a not in full:
                    full.append(a)
            if idd not in full:
                full.append(idd)

        presets.append({'id': slug(pr['title']), 'label': pr['title'], 'chapters': full})
        print(f'  {pr["title"]}: {len(full)} chapitres')

    OUTPUT_PATH.write_text(
        json.dumps({'presets': presets}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(f'\nÉcrit : {OUTPUT_PATH}')
    if warnings:
        print('\nAvertissements (à vérifier) :')
        for title, level, label, w in warnings:
            print(f'  [{title[:25]}] L{level} {label!r} -> {w}')


if __name__ == '__main__':
    main()
