# Presets « type de projet » — décisions de correspondance à valider

## Contexte (à relire en début de session)

La vue de sélection propose une **liste déroulante de types de projet**. Quand on
en choisit un, les cases à cocher des chapitres associés se cochent
automatiquement (comportement : **remplace** la sélection en cours ; les annexes
ne sont pas touchées).

Les 3 types proviennent de `exemple-sommaire.docx` (3 tables des matières,
styles `TM1`/`TM2`/`TM3`) :

1. **MISSION G2 PRO – Bâtiment d'activités**
2. **MAISON INDIVIDUELLE AVEC SOUS-SOL**
3. **MAISON INDIVIDUELLE SANS SOUS-SOL**

Chaque entrée de ces sommaires doit pointer vers un chapitre du modèle
`modele_word_atlantis.docx` (ids `h1_*`, `h2_*`, `h3_*`).

### La difficulté
Les libellés des sommaires **ne correspondent pas proprement** au modèle :
- le modèle a des formes génériques avec « / » et « (s) » (`Projet/Contexte du projet`) ;
- la hiérarchie diffère (un chapitre du sommaire peut être un sous-chapitre du modèle, et inversement) ;
- certaines entrées des sommaires **n'existent pas** dans le modèle.

Le rapprochement est donc fait par un script
(`scripts/build_presets.py`) : normalisation + désambiguïsation par contexte
parent + une table d'**OVERRIDES** pour les cas que la machine ne peut pas
trancher. Le résultat est écrit dans `backend/project_presets.json`, chargé par
le backend au startup et servi via `GET /api/presets`.

### Comment mettre à jour le mapping
1. Éditer la table `OVERRIDES_RAW` dans `scripts/build_presets.py` (clé = libellé
   exact du sommaire, valeur = liste d'ids du modèle ; `[]` = ignorer).
2. Régénérer : `venv/bin/python scripts/build_presets.py`
3. Redémarrer le service : `sudo systemctl restart rapport-generator`

---

## Décisions EN ATTENTE de validation par le propriétaire du site

> Pour chaque point : la **valeur par défaut actuellement appliquée** est indiquée,
> puis une ligne `RÉPONSE :` à compléter. Une fois validé, reporter le choix dans
> `OVERRIDES_RAW` (`scripts/build_presets.py`) et régénérer.

### 1. « Textes réglementaires » et « Documents communiqués »
Présentes dans *G2 PRO* et *Maison avec sous-sol*. **Aucun équivalent direct** dans
le modèle. Chapitre le plus proche : `h1_2` « Références et règles de calcul »
→ `h2_1` « Règles de calcul », `h2_2` « Références ».

Options :
- (a) Cocher `h1_2` + `h2_1` + `h2_2` (large) ← **défaut appliqué**
- (b) Cocher `h2_2` « Références » seule (+ parent `h1_2`)
- (c) Ignorer (ne rien cocher)

RÉPONSE : _______________

### 2. « Agressivité des sols vis-à-vis du béton »
Présente dans *G2 PRO*. Le modèle a :
- `h2_29` « Agressivité du milieu vis-à-vis du béton » (section complète)
  - `h3_1` « Agressivité de l'eau souterraine vis-à-vis du béton »
  - `h3_2` « Agressivité du/des sol(s) vis-à-vis du béton »
  - `h3_3` « Conclusion »

Options :
- (a) `h3_2` seul (sols uniquement, + parent `h2_29`) ← **défaut appliqué**
- (b) `h2_29` (section complète : eau + sols + conclusion)

RÉPONSE : _______________

### 3. « Contexte de mitoyenneté entre fondations voisines »
Présente dans *Maison sans sous-sol*. **Aucun équivalent** dans le modèle.
Le plus proche thématiquement (sujet différent) : `h1_25` « Reprise en sous-œuvre
au niveau du/des bâtiment(s) mitoyen(s) ».

Options :
- (a) Ignorer (ne rien cocher) ← **défaut appliqué**
- (b) Mapper vers `h1_25`

RÉPONSE : _______________

---

## Correspondances déjà figées (non bloquantes — pour info / vérification)

Cas où le libellé du sommaire diffère du modèle mais où la correspondance est
fiable (variantes pluriel/préfixe/slash). Définies dans `OVERRIDES_RAW`.

| Libellé sommaire | → Modèle |
|---|---|
| Cadre de l'étude | *(regroupement du sommaire — ignoré, ses sous-items sont mappés)* |
| Synthèse des essais en laboratoire | `h1_13` Synthèse de(s) l'essai(s) en laboratoire |
| Identifications G.T.R. | `h2_18` Identification(s) G.T.R. |
| Analyses physico-chimiques | `h2_21` Analyse(s) physico-chimique(s) |
| Masses volumiques | `h2_25` Masse(s) volumique(s) |
| Synthèse des fouilles à la pelle mécanique | `h1_14` |
| Plateforme des dallages et des voiries | `h1_23` |
| Dimensionnement des inclusions rigides | `h2_54` Pré-dimensionnement des inclusions rigides |
| Paramètres de dimensionnement (ctx inclusions) | `h3_17` Paramètres de pré-dimensionnement |
| Dallages | `h1_31` |
| Terrassement du bassin | `h1_32` |
| Dimensionnement des voiries | `h2_84` Pré-dimensionnement des voiries |
| Aléas et risques résiduels | `h1_45` Aléas et risques identifiés/résiduels |
| Pré-dimensionnement des fondations superficielles | `h2_53` |
| Dimensionnent de fondations superficielles *(typo)* | `h2_53` |
| Estimation des tassements des fondations | `h3_14` |

Les libellés répétés dans le modèle (`Sujétions d'exécutions`, `Capacité
portante`, `Estimation des tassements`, `Excavation des terres`, `Fondations`…)
sont résolus automatiquement par le **contexte parent** dans le sommaire.
