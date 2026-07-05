"""
Filtrage d'un .docx ATLANTIS selon une sélection de chapitres et d'annexes.

Algorithme :
  1. unpack du .docx (skill office/unpack.py)
  2. parse document.xml et calcule des « owners » par enfant du body
  3. décide pour chaque enfant s'il doit être conservé selon la sélection
  4. override sectPr : si un paragraphe portant un sectPr inline doit être
     supprimé alors que sa section termine au moins un paragraphe conservé,
     on le conserve (en vidant ses runs) pour préserver l'orientation
  5. renumérotation des annexes conservées (1, 2, 3…) : le numéro, produit par un
     champ PAGE dans le modèle, est figé en texte statique (saut de ligne préservé)
  6. suppression des enfants marqués
  7. fix_rels + updateFields (le sommaire des annexes reste un vrai champ TOC,
     rafraîchi par Word à l'ouverture) + repack (skill office/pack.py)
"""

import os
import re
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from lxml import etree

from extractor import ANNEX_RE, analyze, _get_style, _is_toc_style

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


def _neutralize_number_field(p, new_num: int) -> bool:
    """Titre d'annexe ATLANTIS : le numéro est produit par un champ (PAGE), suivi d'un
    <w:br/> puis du libellé. On remplace TOUT le champ (runs fldChar begin..end) par un
    unique run statique contenant `new_num`, en conservant le formatage (rPr) du run
    résultat. Le <w:br/> et le libellé restent intacts → numéro et titre sur deux lignes,
    et le numéro devient du texte figé (immunisé contre le rafraîchissement des champs).
    Retourne True si un champ a été neutralisé.
    """
    runs = p.findall(f'{WNS}r')
    begin_i = sep_i = end_i = None
    for i, r in enumerate(runs):
        for fc in r.findall(f'{WNS}fldChar'):
            kind = fc.get(f'{WNS}fldCharType')
            # 'begin' et 'separate' peuvent partager un même run après normalisation
            # par unpack.py (d'où le >= et non > dans les comparaisons d'indices).
            if kind == 'begin' and begin_i is None:
                begin_i = i
            elif kind == 'separate':
                sep_i = i
            elif kind == 'end':
                end_i = i
        if end_i is not None:
            break
    if begin_i is None or end_i is None or begin_i >= end_i:
        return False

    # Runs résultat = ceux entre 'separate' (ou 'begin' si pas de separate) et 'end',
    # exclus. On y prend le rPr du 1er run avec texte pour préserver la mise en forme.
    res_start = (sep_i if sep_i is not None else begin_i) + 1
    result_rpr = None
    for r in runs[res_start:end_i]:
        if r.find(f'{WNS}t') is not None:
            result_rpr = r.find(f'{WNS}rPr')
            break

    new_r = etree.Element(f'{WNS}r')
    if result_rpr is not None:
        new_r.append(deepcopy(result_rpr))
    t = etree.SubElement(new_r, f'{WNS}t')
    t.set(XML_SPACE, 'preserve')
    t.text = str(new_num)

    field_runs = runs[begin_i:end_i + 1]
    field_runs[0].addprevious(new_r)
    for r in field_runs:
        p.remove(r)
    return True


def _renumber_annex_text(p, old_num: int, new_num: int) -> bool:
    """Fallback : numéro en texte figé (pas de champ). Remplace « Annexe n°<old> » en
    ne modifiant QUE les <w:t> chevauchant le motif — les runs suivants (dont le
    <w:br/> et le libellé) restent en place."""
    pattern = re.compile(r'Annexe\s*n[°º]\s*' + str(old_num) + r'(?!\d)', re.IGNORECASE)
    replacement = f'Annexe n°{new_num}'
    ts = p.findall(f'.//{WNS}t')
    texts = [t.text or '' for t in ts]
    m = pattern.search(''.join(texts))
    if not m:
        return False
    s, e = m.span()
    pos = 0
    done = False
    for t, txt in zip(ts, texts):
        n_start, n_end = pos, pos + len(txt)
        pos = n_end
        if n_end <= s or n_start >= e:
            continue  # run hors du motif : intact
        pre = txt[:s - n_start] if n_start < s else ''
        post = txt[e - n_start:] if n_end > e else ''
        if not done:
            t.text = pre + replacement + post
            done = True
        else:
            t.text = pre + post
    return True


def _renumber_annex_paragraph(p, old_num: int, new_num: int):
    """Fixe le numéro d'un titre d'annexe conservé à `new_num`, en préservant la mise
    en page (saut de ligne numéro / libellé). Neutralise d'abord le champ PAGE ; à
    défaut, remplace le texte figé."""
    if _neutralize_number_field(p, new_num):
        return
    _renumber_annex_text(p, old_num, new_num)


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

    Le sommaire des annexes reste un vrai champ TOC Word (`\\t "Titre annexes;1"`,
    entrées en style TM1) : il est rafraîchi à l'ouverture via updateFields, comme
    le sommaire général. Le numéro de chaque annexe conservée est figé en texte
    statique (voir _neutralize_number_field) pour ne pas être recalculé en n° de page.
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
            # par Word, styles TOC* / TM*) : seules les vraies annexes du corps sont remappées.
            if _is_toc_style(_get_style(child)):
                continue
            text = _get_text(child)
            m = ANNEX_RE.match(text) if text else None
            if m:
                old_num = int(m.group(1))
                # Toujours traiter les annexes conservées (même si le numéro ne change
                # pas) : ça fige le champ PAGE du numéro pour qu'il ne soit pas recalculé
                # en numéro de page au rafraîchissement des champs.
                if old_num in annex_remap:
                    _renumber_annex_paragraph(child, old_num, annex_remap[old_num])

    # Suppression (en ordre décroissant pour ne pas perturber les indices)
    for i in range(len(children) - 1, -1, -1):
        if not keep[i]:
            body.remove(children[i])

    _fix_rels(unpack_dir)
    _force_update_fields(unpack_dir)
    tree.write(doc_path, xml_declaration=True, encoding='UTF-8', pretty_print=True)
    _pack(unpack_dir, output_path, docx_path)
