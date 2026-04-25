import zipfile
from lxml import etree

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def _get_style(para):
    pPr = para.find(f'{{{W}}}pPr')
    if pPr is not None:
        pStyle = pPr.find(f'{{{W}}}pStyle')
        if pStyle is not None:
            return pStyle.get(f'{{{W}}}val', '')
    return ''


def _get_text(para):
    return ''.join(t.text or '' for t in para.findall(f'.//{{{W}}}t')).strip()


def extract_structure(docx_path: str) -> list[dict]:
    """
    Parcourt le body du document et retourne la liste ordonnée des éléments :
    sections (Titre), chapitres (Titre1) et annexes (Titreannexes).
    """
    with zipfile.ZipFile(docx_path) as z:
        with z.open('word/document.xml') as f:
            tree = etree.parse(f)

    body = tree.getroot().find(f'{{{W}}}body')
    items = []
    chapter_num = 0
    annex_num = 0
    current_section = None

    for para in body:
        if etree.QName(para.tag).localname != 'p':
            continue

        style = _get_style(para)
        text = _get_text(para)

        if not text:
            continue

        if style == 'Titre':
            current_section = text
            items.append({
                'id': f'section_{len(items)}',
                'type': 'section',
                'label': text,
            })

        elif style == 'Titre1':
            chapter_num += 1
            items.append({
                'id': f'ch_{chapter_num}',
                'type': 'chapter',
                'num': chapter_num,
                'label': text,
                'section': current_section,
            })

        elif style == 'Titreannexes':
            annex_num += 1
            items.append({
                'id': f'annex_{annex_num}',
                'type': 'annex',
                'num': annex_num,
                'label': text,
            })

    return items
