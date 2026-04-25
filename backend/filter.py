import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from lxml import etree

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
MAIN_SECTION_STYLES = {'Titre'}
SCRIPTS_DIR = Path(__file__).parent / 'docx_scripts' / 'office'


def _get_style(para):
    pPr = para.find(f'{{{W}}}pPr')
    if pPr is not None:
        pStyle = pPr.find(f'{{{W}}}pStyle')
        if pStyle is not None:
            return pStyle.get(f'{{{W}}}val', '')
    return ''


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
    """Corrige les références locales Windows qui bloquent la validation."""
    rels_path = os.path.join(unpack_dir, 'word', '_rels', 'document.xml.rels')
    if not os.path.exists(rels_path):
        return
    with open(rels_path, 'r', encoding='utf-8') as f:
        content = f.read()
    fixed = re.sub(r'Target="file:///[^"]*"', 'Target="https://example.com"', content)
    if fixed != content:
        with open(rels_path, 'w', encoding='utf-8') as f:
            f.write(fixed)


def filter_document(
    docx_path: str,
    selected_chapters: set[int],
    selected_annexes: set[int],
    unpack_dir: str,
    output_path: str,
):
    """
    Génère un nouveau .docx ne contenant que les chapitres et annexes sélectionnés.
    selected_chapters : ensemble de numéros de chapitres (1-based, Titre1)
    selected_annexes  : ensemble de numéros d'annexes   (1-based, Titreannexes)
    """
    _unpack(docx_path, unpack_dir)

    doc_path = os.path.join(unpack_dir, 'word', 'document.xml')
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(doc_path, parser)
    body = tree.getroot().find(f'{{{W}}}body')
    children = list(body)

    # ── Passe 1 : numéroter chapitres et annexes ─────────────────────────
    chapter_num = 0
    annex_num = 0
    child_chapter = {}   # index -> numéro chapitre
    child_annex = {}     # index -> numéro annexe

    for i, child in enumerate(children):
        if etree.QName(child.tag).localname != 'p':
            continue
        style = _get_style(child)
        if style == 'Titre1':
            chapter_num += 1
            child_chapter[i] = chapter_num
        elif style == 'Titreannexes':
            annex_num += 1
            child_annex[i] = annex_num

    # ── Passe 2 : marquer les éléments à supprimer ──────────────────────
    to_remove = set()
    in_remove = False

    for i, child in enumerate(children):
        local = etree.QName(child.tag).localname

        if local == 'sectPr':
            continue

        if i in child_chapter:
            in_remove = child_chapter[i] not in selected_chapters
            if in_remove:
                to_remove.add(i)

        elif i in child_annex:
            in_remove = child_annex[i] not in selected_annexes
            if in_remove:
                to_remove.add(i)

        elif local == 'p' and _get_style(child) in MAIN_SECTION_STYLES:
            in_remove = False

        elif in_remove:
            to_remove.add(i)

    # ── Suppression ──────────────────────────────────────────────────────
    for i in sorted(to_remove, reverse=True):
        body.remove(children[i])

    _fix_rels(unpack_dir)
    tree.write(doc_path, xml_declaration=True, encoding='UTF-8', pretty_print=True)
    _pack(unpack_dir, output_path, docx_path)
