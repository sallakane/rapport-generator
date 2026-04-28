"""
Parse le modèle ATLANTIS et expose :
  - parse(docx_path) -> ParsedDoc : représentation interne (utilisée par filter.py)
  - to_api_structure(parsed) -> dict : structure consommée par le frontend

Convention de styles attendue :
  - 'Title'    : section principale (non cochable, regroupement visuel)
  - 'Heading1' : chapitre cochable
  - 'Heading2' : sous-chapitre cochable
  - 'Heading3' : sous-sous-chapitre cochable
  - 'Heading4'+ : ignorés (pas dans le modèle ATLANTIS, fallback : traités comme contenu)

Annexes : aucun style spécifique. Détectées par regex sur le texte du paragraphe :
  ^Annexe n°<num><label>  (ex: "Annexe n°5Coupe(s) du/des sondage(s)…")
"""

import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
WNS = f'{{{W}}}'

ANNEX_RE = re.compile(r'^\s*Annexe\s*n[°º]\s*(\d+)', re.IGNORECASE)


def _get_style(p) -> str:
    pPr = p.find(f'{WNS}pPr')
    if pPr is None:
        return ''
    st = pPr.find(f'{WNS}pStyle')
    if st is None:
        return ''
    return st.get(f'{WNS}val', '')


def _get_text(p) -> str:
    return ''.join(t.text or '' for t in p.findall(f'.//{WNS}t')).strip()


def _is_p(el) -> bool:
    return etree.QName(el.tag).localname == 'p'


@dataclass
class ParsedDoc:
    """
    Représentation interne du modèle, utilisée par filter.py.
    `owners[i]` indique le nœud auquel appartient le i-ème enfant du body :
      - ('cover',)              : page de garde (avant 1er Title)
      - ('section', idx)        : paragraphe Title lui-même
      - ('h1', n)               : chapitre H1 (n = compteur 1-based)
      - ('h2', n)               : sous-chapitre H2
      - ('h3', n)               : sous-sous-chapitre H3
      - ('annex', num)          : paragraphe d'annexe (num = numéro original)
      - ('annex_orphan',)       : zone annexes mais pas dans une annexe identifiée (rare)
    """
    children_count: int
    owners: list[tuple]
    sections: list[dict]            # arbre Title → H1 → H2 → H3
    annexes: list[dict]             # liste plate des annexes
    first_annex_idx: int | None     # index du premier paragraphe d'annexe dans body


def parse(docx_path: str) -> ParsedDoc:
    with zipfile.ZipFile(docx_path) as z:
        with z.open('word/document.xml') as f:
            tree = etree.parse(f)
    return analyze(tree.getroot().find(f'{WNS}body'))


def analyze(body) -> ParsedDoc:
    """Analyse un <w:body> lxml et renvoie un ParsedDoc.
    Utilisé par parse() au startup et par filter.py (qui travaille sur un body unpacké).
    """
    children = list(body)

    sections: list[dict] = []
    annexes: list[dict] = []
    owners: list[tuple] = [('cover',)] * len(children)

    section_idx = -1
    h1_idx = 0
    h2_idx = 0
    h3_idx = 0

    current_section: dict | None = None
    current_h1: dict | None = None
    current_h2: dict | None = None
    current_h3: dict | None = None

    in_annex_zone = False
    current_annex_num: int | None = None
    first_annex_idx: int | None = None

    for i, child in enumerate(children):
        if not _is_p(child):
            # Tables, sectPr final, etc. → on hérite de l'owner du précédent
            owners[i] = owners[i - 1] if i > 0 else ('cover',)
            continue

        text = _get_text(child)
        style = _get_style(child)

        # Détection annexe par regex sur le texte (les annexes n'ont pas de style dédié)
        m = ANNEX_RE.match(text) if text else None
        if m:
            if first_annex_idx is None:
                first_annex_idx = i
            in_annex_zone = True
            num = int(m.group(1))
            label = ANNEX_RE.sub('', text, count=1).strip()
            annexes.append({
                'id': f'annex_{num}',
                'num': num,
                'label': label,
            })
            current_annex_num = num
            owners[i] = ('annex', num)
            continue

        if in_annex_zone:
            # Contenu suivant le titre d'annexe (corps de l'annexe)
            owners[i] = ('annex', current_annex_num) if current_annex_num is not None else ('annex_orphan',)
            continue

        if style == 'Title':
            section_idx += 1
            current_section = {
                'id': f'section_{section_idx}',
                'label': text,
                'chapters': [],
            }
            sections.append(current_section)
            current_h1 = current_h2 = current_h3 = None
            owners[i] = ('section', section_idx)
            continue

        if style == 'Heading1':
            h1_idx += 1
            current_h1 = {
                'id': f'h1_{h1_idx}',
                'label': text,
                'children': [],
            }
            if current_section is None:
                # H1 avant tout Title : section implicite
                section_idx += 1
                current_section = {'id': f'section_{section_idx}', 'label': '', 'chapters': []}
                sections.append(current_section)
            current_section['chapters'].append(current_h1)
            current_h2 = current_h3 = None
            owners[i] = ('h1', h1_idx)
            continue

        if style == 'Heading2':
            h2_idx += 1
            current_h2 = {
                'id': f'h2_{h2_idx}',
                'label': text,
                'children': [],
            }
            if current_h1 is not None:
                current_h1['children'].append(current_h2)
            current_h3 = None
            owners[i] = ('h2', h2_idx)
            continue

        if style == 'Heading3':
            h3_idx += 1
            current_h3 = {
                'id': f'h3_{h3_idx}',
                'label': text,
            }
            if current_h2 is not None:
                current_h2['children'].append(current_h3)
            owners[i] = ('h3', h3_idx)
            continue

        # Paragraphe ordinaire : owner = nœud le plus profond actif
        if current_h3 is not None:
            owners[i] = ('h3', h3_idx)
        elif current_h2 is not None:
            owners[i] = ('h2', h2_idx)
        elif current_h1 is not None:
            owners[i] = ('h1', h1_idx)
        elif current_section is not None:
            owners[i] = ('section', section_idx)
        else:
            owners[i] = ('cover',)

    return ParsedDoc(
        children_count=len(children),
        owners=owners,
        sections=sections,
        annexes=annexes,
        first_annex_idx=first_annex_idx,
    )


def to_api_structure(parsed: ParsedDoc) -> dict[str, Any]:
    """Renvoyé par GET /api/structure — consommé par le frontend."""
    return {
        'sections': parsed.sections,
        'annexes': parsed.annexes,
    }
