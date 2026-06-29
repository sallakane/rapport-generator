"""
Filtrage d'un .docx ATLANTIS selon une sélection de chapitres et d'annexes.

Algorithme :
  1. unpack du .docx (skill office/unpack.py)
  2. parse document.xml et calcule des « owners » par enfant du body
  3. décide pour chaque enfant s'il doit être conservé selon la sélection
  4. override sectPr : si un paragraphe portant un sectPr inline doit être
     supprimé alors que sa section termine au moins un paragraphe conservé,
     on le conserve (en vidant ses runs) pour préserver l'orientation
  5. renumérotation des paragraphes « Annexe n°X » conservés (1, 2, 3…)
  6. suppression des enfants marqués
  7. fix_rels + repack (skill office/pack.py)
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from lxml import etree

from extractor import ANNEX_RE, analyze, _get_style

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
WNS = f'{{{W}}}'
SCRIPTS_DIR = Path(__file__).parent / 'docx_scripts' / 'office'


def _is_p(el) -> bool:
    return etree.QName(el.tag).localname == 'p'


def _get_text(p) -> str:
    return ''.join(t.text or '' for t in p.findall(f'.//{WNS}t')).strip()


def _has_inline_sectPr(p) -> bool:
    pPr = p.find(f'{WNS}pPr')
    return pPr is not None and pPr.find(f'{WNS}sectPr') is not None


def _unpack(docx_path: str, unpack_dir: str):
    if os.path.exists(unpack_dir):
        shutil.rmtree(unpack_dir)
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / 'unpack.py'), docx_path, unpack_dir],
        check=True, capture_output=True,
    )


def _pack(unpack_dir: str, output_path: str, original_path: str):
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / 'pack.py'),
         unpack_dir, output_path, '--original', original_path],
        check=True, capture_output=True,
    )


def _fix_rels(unpack_dir: str):
    """Neutralise les Targets que le validateur du skill pack rejette :
    - Target="file:///..." (références locales Windows)
    - Target="about:blank" (hyperliens vides générés par Word)
    """
    rels_path = os.path.join(unpack_dir, 'word', '_rels', 'document.xml.rels')
    if not os.path.exists(rels_path):
        return
    with open(rels_path, 'r', encoding='utf-8') as f:
        content = f.read()
    fixed = re.sub(r'Target="file:///[^"]*"', 'Target="https://example.com"', content)
    fixed = re.sub(r'Target="about:blank"', 'Target="https://example.com"', fixed)
    if fixed != content:
        with open(rels_path, 'w', encoding='utf-8') as f:
            f.write(fixed)


def _force_update_fields(unpack_dir: str):
    """Force Word à recalculer tous les champs à l'ouverture (<w:updateFields val=true>).
    Indispensable car les deux sommaires (chapitres + table des annexes) sont des
    champs TOC : sans rafraîchissement, Word afficherait le cache du modèle (tous
    les chapitres / 24 annexes) au lieu de la sélection réellement conservée.
    """
    settings_path = os.path.join(unpack_dir, 'word', 'settings.xml')
    if not os.path.exists(settings_path):
        return
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(settings_path, parser)
    root = tree.getroot()
    if root.find(f'{WNS}updateFields') is not None:
        return
    el = etree.Element(f'{WNS}updateFields')
    el.set(f'{WNS}val', 'true')
    # CT_Settings impose un ordre : updateFields se place juste avant ces éléments
    # « tardifs ». On l'insère devant le premier présent, sinon en fin.
    after = {'hdrShapeDefaults', 'footnotePr', 'endnotePr', 'compat', 'rsids',
             'mathPr', 'uiCompat97To2003', 'attachedSchema', 'themeFontLang',
             'clrSchemeMapping', 'doNotIncludeSubdocsInStats',
             'doNotAutoCompressPictures', 'forceUpgrade', 'captions',
             'readModeInkLockDown', 'smartTagType', 'schemaLibrary',
             'shapeDefaults', 'doNotEmbedSmartTags', 'decimalSymbol',
             'listSeparator'}
    pos = len(root)
    for i, child in enumerate(root):
        if etree.QName(child.tag).localname in after:
            pos = i
            break
    root.insert(pos, el)
    tree.write(settings_path, xml_declaration=True, encoding='UTF-8', standalone=True)


def _decide_keep(
    owner: tuple,
    selected_chapters: set[str],
    selected_annexes: set[int],
    sections_kept: set[int],
) -> bool:
    """Décide si un enfant du body doit être conservé selon son owner."""
    kind = owner[0]
    if kind == 'cover':
        return True
    if kind == 'section':
        return owner[1] in sections_kept
    if kind == 'h1':
        return f'h1_{owner[1]}' in selected_chapters
    if kind == 'h2':
        return f'h2_{owner[1]}' in selected_chapters
    if kind == 'h3':
        return f'h3_{owner[1]}' in selected_chapters
    if kind == 'annex':
        return owner[1] in selected_annexes
    return False  # annex_orphan : drop


def _clear_runs_keep_pPr(p):
    """Vide les runs d'un paragraphe en conservant son pPr (et donc le sectPr)."""
    for child in list(p):
        if etree.QName(child.tag).localname != 'pPr':
            p.remove(child)


XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'


def _make_toc1_entry(text: str):
    """Construit un paragraphe statique style TOC1 contenant `text` (pas de champ)."""
    p = etree.Element(f'{WNS}p')
    pPr = etree.SubElement(p, f'{WNS}pPr')
    pStyle = etree.SubElement(pPr, f'{WNS}pStyle')
    pStyle.set(f'{WNS}val', 'TOC1')
    r = etree.SubElement(p, f'{WNS}r')
    t = etree.SubElement(r, f'{WNS}t')
    t.set(XML_SPACE, 'preserve')
    t.text = text
    return p


def _rebuild_annex_toc(body, kept_annex_nums: list[int], annex_remap: dict[int, int],
                       label_by_num: dict[int, str]):
    """Remplace le champ TOC des annexes par une liste statique des annexes conservées.

    Le sommaire des annexes du modèle est un champ `TOC \\n \\h \\z \\t "Titre annexes;1"`
    SANS numéro de page (\\n) : c'est donc une simple liste de titres. Plutôt que de
    laisser Word le rafraîchir (comportement bancal sur ce modèle : il reprend le
    sommaire général), on le remplace par des paragraphes figés générés à partir des
    annexes réellement cochées et renumérotées. Aucune perte (pas de pagination à
    recalculer) et plus rien à actualiser côté utilisateur.
    """
    kids = list(body)

    # 1. Localiser le paragraphe qui ouvre le champ TOC des annexes (instrText \t "Titre annexes…")
    start = None
    for idx, el in enumerate(kids):
        if not _is_p(el):
            continue
        instr = ''.join(t.text or '' for t in el.findall(f'.//{WNS}instrText'))
        if 'TOC' in instr and 'Titre annexes' in instr:
            start = idx
            break
    if start is None:
        return  # pas de champ TOC annexes (modèle différent) : rien à faire

    # 2. Trouver la fin du champ via le compteur de fldChar begin/end
    depth = 0
    seen_begin = False
    end = start
    for idx in range(start, len(kids)):
        el = kids[idx]
        if not _is_p(el):
            continue
        for fc in el.findall(f'.//{WNS}fldChar'):
            kind = fc.get(f'{WNS}fldCharType')
            if kind == 'begin':
                depth += 1
                seen_begin = True
            elif kind == 'end':
                depth -= 1
        if seen_begin and depth == 0:
            end = idx
            break

    # 3. Construire les entrées statiques (ordre des nouveaux numéros 1, 2, 3…)
    new_paras = []
    for old in kept_annex_nums:
        new_num = annex_remap.get(old, old)
        label = label_by_num.get(old, '').strip()
        text = f'Annexe n°{new_num} {label}'.rstrip()
        new_paras.append(_make_toc1_entry(text))

    # 4. Remplacer le bloc [start..end] par les entrées statiques
    insert_pos = list(body).index(kids[start])
    for el in kids[start:end + 1]:
        body.remove(el)
    for offset, p in enumerate(new_paras):
        body.insert(insert_pos + offset, p)


def _renumber_annex_paragraph(p, old_num: int, new_num: int):
    """Réécrit « Annexe n°<old_num> » en « Annexe n°<new_num> » dans le paragraphe.
    Cherche d'abord un <w:t> unique contenant le pattern complet, fallback cross-run."""
    pattern = re.compile(
        r'Annexe\s*n[°º]\s*' + str(old_num) + r'(?!\d)',
        re.IGNORECASE,
    )
    replacement = f'Annexe n°{new_num}'

    # Cas simple : pattern dans un seul <w:t>
    for t in p.findall(f'.//{WNS}t'):
        if t.text and pattern.search(t.text):
            t.text = pattern.sub(replacement, t.text, count=1)
            return

    # Fallback : pattern réparti sur plusieurs runs
    ts = p.findall(f'.//{WNS}t')
    full = ''.join(t.text or '' for t in ts)
    if not pattern.search(full):
        return  # paragraphe annexe non standard, on laisse tel quel
    new_full = pattern.sub(replacement, full, count=1)
    if ts:
        ts[0].text = new_full
        for t in ts[1:]:
            t.text = ''


def filter_document(
    docx_path: str,
    selected_chapters: set[str],
    selected_annexes: set[int],
    unpack_dir: str,
    output_path: str,
):
    """
    Génère un .docx filtré selon la sélection.
    selected_chapters : ids cochés (h1_*, h2_*, h3_*) — la cohérence parent/enfant
                        est garantie par le frontend (cascade)
    selected_annexes  : numéros d'annexes originaux à conserver
    """
    _unpack(docx_path, unpack_dir)

    doc_path = os.path.join(unpack_dir, 'word', 'document.xml')
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(doc_path, parser)
    body = tree.getroot().find(f'{WNS}body')
    children = list(body)

    parsed = analyze(body)

    # Sections conservées : TOC (sans chapters) toujours conservées,
    # autres sections conservées si au moins un chapter sélectionné
    sections_kept: set[int] = set()
    for i, sec in enumerate(parsed.sections):
        if not sec['chapters']:
            sections_kept.add(i)  # TOC type SOMMAIRE / table des annexes
            continue
        for ch in sec['chapters']:
            if ch['id'] in selected_chapters:
                sections_kept.add(i)
                break

    # Décision initiale par enfant
    keep = [
        _decide_keep(parsed.owners[i], selected_chapters, selected_annexes, sections_kept)
        for i in range(len(children))
    ]

    # Override sectPr : préserver l'orientation
    prev_sectpr = -1
    for i, child in enumerate(children):
        if not _is_p(child) or not _has_inline_sectPr(child):
            continue
        # range de la section : (prev_sectpr+1) .. i  (paragraphes uniquement)
        section_has_kept = any(
            keep[j] for j in range(prev_sectpr + 1, i)
            if _is_p(children[j])
        )
        if not keep[i] and section_has_kept:
            keep[i] = True
            _clear_runs_keep_pPr(child)
        prev_sectpr = i

    # Renumérotation des annexes conservées.
    # On part directement de la sélection utilisateur (triée), pas de la table `keep` :
    # `keep` peut avoir été modifiée par l'override sectPr et faire passer pour
    # « conservé » un paragraphe d'annexe non sélectionnée (ses runs sont en fait vidés).
    kept_annex_nums = sorted(selected_annexes)
    annex_remap = {old: new for new, old in enumerate(kept_annex_nums, start=1)}

    if annex_remap:
        for i, child in enumerate(children):
            if not keep[i] or not _is_p(child):
                continue
            # Ne pas renuméroter les lignes de sommaire (table des annexes générée
            # par Word, styles TOC*) : seules les vraies annexes du corps sont remappées.
            if _get_style(child).startswith('TOC'):
                continue
            text = _get_text(child)
            m = ANNEX_RE.match(text) if text else None
            if m:
                old_num = int(m.group(1))
                if old_num in annex_remap and annex_remap[old_num] != old_num:
                    _renumber_annex_paragraph(child, old_num, annex_remap[old_num])

    # Suppression (en ordre décroissant pour ne pas perturber les indices)
    for i in range(len(children) - 1, -1, -1):
        if not keep[i]:
            body.remove(children[i])

    # Remplace le champ TOC des annexes par une liste statique (re-scan du body
    # car les indices `children` ci-dessus ne sont plus valides après suppression).
    label_by_num = {a['num']: a['label'] for a in parsed.annexes}
    _rebuild_annex_toc(body, kept_annex_nums, annex_remap, label_by_num)

    _fix_rels(unpack_dir)
    _force_update_fields(unpack_dir)
    tree.write(doc_path, xml_declaration=True, encoding='UTF-8', pretty_print=True)
    _pack(unpack_dir, output_path, docx_path)
