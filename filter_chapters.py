#!/usr/bin/env python3
"""
Script pour supprimer les chapitres non désirés du document ATLANTIS.
Conserve : ch 1-15, 20-27, 33, 36, 40-45, et toutes les annexes.
Utilise lxml pour préserver les namespaces exactement.
"""
from lxml import etree

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

# Chapitres à conserver
KEEP = set(list(range(1, 16)) + list(range(20, 28)) + [33, 36] + list(range(40, 46)))

# Styles de sections principales (reset du flag de suppression)
MAIN_SECTION_STYLES = {'Titre', 'Titreannexes'}


def get_style(para):
    pPr = para.find(f'{{{W}}}pPr')
    if pPr is not None:
        pStyle = pPr.find(f'{{{W}}}pStyle')
        if pStyle is not None:
            return pStyle.get(f'{{{W}}}val', '')
    return ''


def main():
    doc_path = 'unpacked_atlantis/word/document.xml'

    # Parser avec lxml (préserve les namespaces et l'encodage)
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(doc_path, parser)
    root = tree.getroot()
    body = root.find(f'{{{W}}}body')
    children = list(body)

    # ── Passe 1 : numéroter les Titre1 ─────────────────────────────────────
    chapter_num = 0
    child_to_chapter = {}

    for i, child in enumerate(children):
        local = etree.QName(child.tag).localname
        if local == 'p' and get_style(child) == 'Titre1':
            chapter_num += 1
            child_to_chapter[i] = chapter_num

    print(f"Chapitres Titre1 trouvés : {chapter_num}")

    # ── Passe 2 : déterminer les éléments à supprimer ──────────────────────
    to_remove = set()
    in_remove = False

    for i, child in enumerate(children):
        local = etree.QName(child.tag).localname

        # Toujours garder sectPr
        if local == 'sectPr':
            continue

        if i in child_to_chapter:
            chap = child_to_chapter[i]
            in_remove = chap not in KEEP
            if in_remove:
                to_remove.add(i)
        elif local == 'p':
            style = get_style(child)
            if style in MAIN_SECTION_STYLES:
                in_remove = False
            elif in_remove:
                to_remove.add(i)
        elif in_remove:
            to_remove.add(i)

    print(f"Éléments à supprimer : {len(to_remove)}")
    print(f"Éléments conservés   : {len(children) - len(to_remove)}")

    # ── Suppression en ordre inverse ────────────────────────────────────────
    for i in sorted(to_remove, reverse=True):
        body.remove(children[i])

    # ── Écriture avec lxml (préserve les namespaces) ─────────────────────────
    tree.write(doc_path, xml_declaration=True, encoding='UTF-8',
               pretty_print=True)
    print(f"✓ document.xml réécrit")

    # ── Mise à jour forcée du sommaire dans settings.xml ──────────────────
    settings_path = 'unpacked_atlantis/word/settings.xml'
    stree = etree.parse(settings_path, parser)
    sroot = stree.getroot()

    existing = sroot.find(f'{{{W}}}updateFields')
    if existing is None:
        # Insérer updateFields en première position (avant les autres éléments)
        update_el = etree.Element(f'{{{W}}}updateFields')
        update_el.set(f'{{{W}}}val', '1')
        sroot.insert(0, update_el)
        stree.write(settings_path, xml_declaration=True, encoding='UTF-8',
                    pretty_print=True)
        print("→ updateFields ajouté dans settings.xml")
    else:
        print("→ updateFields déjà présent")

    # ── Rapport ─────────────────────────────────────────────────────────────
    removed = sorted(set(range(1, chapter_num + 1)) - KEEP)
    kept = sorted(KEEP.intersection(range(1, chapter_num + 1)))
    print(f"\nChapitres supprimés ({len(removed)}) : {removed}")
    print(f"Chapitres conservés ({len(kept)})  : {kept}")


if __name__ == '__main__':
    main()
