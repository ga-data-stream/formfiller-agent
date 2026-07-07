# Design — Traitement à la chaîne + tri des mails (Plan A)

**Date** : 2026-07-07
**Statut** : validé (design)
**Spec liée** : [Déploiement chez le collègue](2026-07-07-deployment-colleague-workstation-design.md) (Plan B)

## Problème

Le POC traite **un** mail à la fois via une CLI interactive (`cli.py::main`) :
l'utilisateur voit la liste des mails récents de `ligne adressage`, en choisit
un au clavier, et le pipeline le traite. En « production » (sur le poste d'un
collègue disposant de la boîte générique), il faut traiter les demandes **à la
chaîne** — sans intervention — et **trier** chaque mail entre « traité » et
« revue humaine » pour que le collègue voie l'état d'un coup d'œil et qu'aucun
mail ne soit traité deux fois.

## Objectif

Ajouter un **mode batch non-interactif** qui parcourt tous les mails de
`ligne adressage`, exécute le pipeline existant sur chacun, puis **déplace** le
mail vers un sous-dossier Outlook `Traité` ou `Revue humaine` selon le résultat.
Lançable **à la demande** (raccourci) **et** par une **tâche planifiée** de
secours. La CLI interactive actuelle reste inchangée.

## Décisions (issues du brainstorming)

| # | Décision | Choix retenu |
|---|----------|--------------|
| 1 | Déclenchement | Batch à la demande **+** tâche planifiée de secours |
| 2 | Tri | Déplacement entre sous-dossiers Outlook (`Traité` / `Revue humaine`) |
| 3 | Soumission | Auto-soumission au-dessus du `confidence_threshold` |
| 4 | Boîte mail | Compte **principal** du collègue (les demandes y sont **redirigées** — cf. Plan B), pas la boîte partagée |
| 6 | Observabilité | Dossiers Outlook + journal Excel existant + log par run |

## Non-objectifs

- **Pas** d'accès COM à la boîte partagée/générique (Option 2 abandonnée : les
  infos de la boîte générique ne seront pas disponibles). L'outil lit le
  **compte par défaut** ; l'acheminement des mails vers
  `<Inbox>/ligne adressage` est une règle Outlook côté Plan B, hors code.
- **Pas** de démon temps réel : le « à la réception » est couvert par une tâche
  planifiée qui répète le batch en session (cf. Plan B, caveat COM).
- **Pas** de refacto de la CLI interactive en « runner » commun (alternative
  écartée : gain limité, plus de fichiers touchés). Le batch est un module isolé
  qui réutilise le pipeline existant.
- **Pas** de 3ᵉ dossier « Erreur » : les échecs vont en `Revue humaine` (un
  humain doit s'en occuper de toute façon).

## Conception

### 1. `batch.py` — le runner non-interactif (nouveau module)

Entrée console `formfiller-batch` (déclarée dans `pyproject.toml`, section
`[project.scripts]`).

Responsabilités :
1. Charger `config` + `profile`, `load_dotenv()`, construire
   `OutlookEmailSource(subfolder=config.inbox_subfolder)` sur le compte par
   défaut.
2. **Prendre le verrou** (`config.batch_lock_path`) ; si un verrou frais existe
   déjà → log « run déjà en cours » et sortie propre (code 0). Verrou périmé
   (> `lock_stale_seconds`) → ignoré et réécrit. Libéré en `finally`.
3. Charger le **registre** d'`entry_id` traités (`config.processed_ledger_path`,
   JSON : liste d'ids). Registre absent → liste vide.
4. `messages = source.list_recent(config.inbox_list_count)`.
5. Pour chaque mail **non présent au registre** :
   - traiter via le pipeline existant (voir §4, mode non-interactif) → `JobResult` ;
   - **ajouter l'`entry_id` au registre et le persister** (avant le déplacement,
     pour que même un crash pendant le move n'entraîne pas de rejeu) ;
   - `triage.route(source, email.entry_id, result.status, config)` → déplace le
     mail ; en cas d'échec de déplacement, compter « non-déplacé » (l'id reste
     au registre → pas de rejeu) ;
   - incrémenter les compteurs.
6. Écrire le **récap** (`{traités, revue, échecs, non-déplacés, sautés}`) dans le
   log du run et sur la sortie standard.

Chaque itération est enveloppée dans un `try/except` externe : un mail qui lève
une exception inattendue est compté `fail`, routé vers `Revue humaine`, et le
batch **continue**.

### 2. `triage.py` — le routage (nouveau module, pur + délégation)

```python
def target_subfolder(status: str, config: AppConfig) -> str:
    """Dossier cible Outlook pour un statut de JobResult."""
    if status == "success":
        return config.processed_subfolder      # "Traité"
    return config.review_subfolder              # "Revue humaine" (manual | fail)

def route(source, entry_id: str, status: str, config: AppConfig) -> bool:
    """Déplace le mail vers le dossier cible. Retourne True si déplacé."""
    target = target_subfolder(status, config)
    return source.move_to_subfolder(entry_id, target)
```

Aucune logique Outlook ici : `target_subfolder` est une fonction pure (testable
trivialement) et `route` délègue le déplacement à la source.

### 3. `EmailSource` — protocole étendu + `move_to_subfolder`

`email_source.py` :

- **Protocole** `EmailSource` : ajouter
  `def move_to_subfolder(self, entry_id: str, name: str) -> bool: ...`.
- **`FakeEmailSource`** : implémentation en mémoire qui enregistre les
  déplacements (`self.moves: list[tuple[str, str]]`) et retourne `True`
  (ou un échec simulé configurable, pour tester le chemin « non-déplacé »).
- **`OutlookEmailSource.move_to_subfolder`** (méthode fine, COM réel) :
  1. résoudre (ou **créer** si absent) le sous-dossier cible au même niveau que
     le dossier source, via un helper `_resolve_or_create_sibling(name)` ;
  2. `item = self._folder.Session.GetItemFromID(entry_id)` ;
  3. `item.Move(target_folder)` ;
  4. retourner `True` ; toute exception COM → log d'avertissement + `False`
     (jamais de crash du batch).

Le helper de création réutilise l'idiome existant `_resolve_subfolder` (parcours
de `.Folders`), avec `Folders.Add(name)` si le dossier n'existe pas encore.

### 4. Mode non-interactif (réutilisation du pipeline existant)

Le batch réutilise **tel quel** `process_email` (déterministe) ou
`run_agent_pipeline` (agent), selon `config.fill_strategy`. Deux points
d'interactivité à neutraliser :

- **Sélection du mail** : court-circuitée (le batch boucle sur tous les mails).
- **Confirmation avant soumission** (chemin agent) : aujourd'hui
  `cli.py::_terminal_confirm` appelle `input()`. Le batch injecte à la place un
  `confirm` **auto-`True`** (cohérent avec l'auto-soumission décidée). Le
  câblage des hooks/deps est extrait des helpers existants de `cli.py`
  (`_build_hooks`, `_build_agent_deps`, `build_agent_run`) en paramétrant le
  `confirm` — sans dupliquer la construction du client Azure / Playwright.

> Note : `config.dry_run` reste le garde-fou de test. En prod il vaut `false`
> (auto-soumission) ; en `true`, le batch remplit sans soumettre et tout mail
> « rempli mais non soumis » reste néanmoins routé selon son statut (`success`
> en dry-run → `Traité`, avec la mention dry-run déjà présente dans le log).

### 5. Configuration (nouveaux champs `AppConfig`, défauts rétrocompatibles)

`config.py` :

```python
processed_subfolder: str = "Traité"
review_subfolder: str = "Revue humaine"
batch_lock_path: str = "./.batch.lock"
lock_stale_seconds: int = 3600
processed_ledger_path: str = "./processed_ids.json"
run_log_dir: str = "./logs"
```

Ces champs ont des défauts → une config existante (CLI interactive) n'est pas
affectée. Ils seront réglés dans le `config.prod.example.yaml` du Plan B.

### 6. Idempotence & concurrence

- **Principal** : le mail traité quitte `ligne adressage` → invisible au run
  suivant.
- **Filet de sécurité** : le **registre** `processed_ids.json`. Le batch saute
  tout `entry_id` déjà présent, **même si le déplacement a échoué**. Sans ça, un
  move raté = mail rejoué = **formulaire soumis deux fois** (inacceptable en
  auto-soumission). L'id est écrit au registre **avant** le déplacement.
- **Verrou** `batch_lock_path` : empêche le chevauchement run manuel / run
  planifié. Verrou frais → sortie immédiate ; périmé (> `lock_stale_seconds`) →
  ignoré. Libéré en `finally`.

### 7. Observabilité

- **Log par run** : `<run_log_dir>/batch-<horodatage>.log` — une ligne par mail
  (statut, confiance, dossier cible, déplacé O/N) + le récap final.
  Horodatage = `datetime.now()` au démarrage du run.
- **Excel** : inchangé, une ligne par mail (déjà produit par le pipeline).
- **Dossiers Outlook** : `Traité` / `Revue humaine` = l'état visuel du collègue.
- **Récap final** : compteurs `{traités, revue, échecs, non-déplacés, sautés}`.
- **Sécurité** : aucun secret dans les logs (règle CLAUDE.md — jamais
  `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT`).

## Gestion d'erreur (synthèse)

| Situation | Comportement |
|-----------|--------------|
| Mail sans lien de formulaire / erreur read-map | `fail` → `Revue humaine` (déjà géré par `process_email`) |
| Exception inattendue sur un mail | capturée par le `try/except` du batch → `fail` → `Revue humaine`, on continue |
| Déplacement Outlook échoue | log warning, id reste au registre (pas de rejeu), compté « non-déplacé » |
| Run déjà en cours (verrou frais) | sortie propre code 0, message dans le log |
| Registre corrompu / illisible | log warning, on repart d'une liste vide (le déplacement reste le garde-fou visuel) |

## Tests

Le COM Outlook n'est pas testable en CI. On teste la logique avec des fakes
(infra déjà en place : `FakeEmailSource`, `PipelineHooks`). TDD, `pytest` à la
fin.

- `tests/test_triage.py` (nouveau) : `target_subfolder` pour `success` /
  `manual` / `fail` ; `route` appelle `move_to_subfolder` avec le bon dossier.
- `tests/test_batch.py` (nouveau) avec `FakeEmailSource` + pipeline factice :
  - itère tous les mails et route chacun vers le bon dossier ;
  - **idempotence** : un `entry_id` au registre est sauté ; le registre est mis
    à jour après traitement ;
  - **échec de déplacement** : id conservé au registre (pas de rejeu), compté
    « non-déplacé » ;
  - **isolation** : un mail qui lève une exception → `fail` → `Revue humaine`,
    le batch continue et les compteurs sont justes ;
  - **verrou** : un 2ᵉ run avec verrou frais s'arrête proprement ;
  - **récap** : compteurs corrects.
- `tests/test_config.py` : défauts des nouveaux champs + override.
- **Non couvert par les tests (validé au smoke test du Plan B)** : déplacement
  COM live, création de sous-dossier Outlook, appels Azure/Playwright réels.

## Portée des fichiers

**Nouveaux** : `src/formfiller/batch.py`, `src/formfiller/triage.py`,
`tests/test_batch.py`, `tests/test_triage.py`.
**Modifiés** : `src/formfiller/email_source.py` (protocole + `move_to_subfolder`
+ Fake), `src/formfiller/config.py` (nouveaux champs), `pyproject.toml`
(script `formfiller-batch`), `tests/test_config.py`.
Éventuel petit refactor d'extraction dans `cli.py` pour partager la construction
des hooks/deps avec `confirm` paramétrable (sans changer le comportement de la
CLI interactive).

Changement **additif** ; > 3 fichiers touchés → ce design tient lieu de plan
préalable (règle CLAUDE.md). Branche `feat/batch-triage-deployment`.
