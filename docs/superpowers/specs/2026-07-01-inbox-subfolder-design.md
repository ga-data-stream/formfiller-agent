# Design — Lire depuis le sous-dossier Inbox « ligne adressage »

**Date** : 2026-07-01
**Statut** : validé (design)

## Problème

`OutlookEmailSource` (`src/formfiller/email_source.py`) ouvre aujourd'hui la
Boîte de réception par défaut (`GetDefaultFolder(6)`, olFolderInbox) et
`list_recent` liste ses items. Les mails à traiter sont en réalité rangés dans
un **sous-dossier de l'Inbox nommé « ligne adressage »** ; le code n'a pas
besoin de parcourir l'inbox générale.

## Objectif

Faire lire `list_recent` depuis un sous-dossier configurable de l'Inbox, sans
casser le comportement actuel pour qui ne configure rien.

## Non-objectifs

- Pas de support de dossiers racine du compte ni d'imbrication multi-niveaux :
  le dossier cible est un **sous-dossier direct de l'Inbox** (confirmé).
- Pas de changement à `get(entry_id)` : il passe par
  `Session.GetItemFromID`, indépendant du dossier — inchangé (hors renommage
  d'attribut interne).
- Pas de normalisation des accents dans le matching de nom (le libellé exact
  est fourni en config).

## Conception

### 1. Configuration

`src/formfiller/config.py` — nouveau champ sur `AppConfig` :

```python
inbox_subfolder: str = ""   # vide → Inbox racine (rétrocompatible)
```

`config.yaml` — réglé sur la valeur réelle :

```yaml
inbox_subfolder: "ligne adressage"   # sous-dossier de la Boîte de réception à lire
```

Défaut `""` = comportement actuel (Inbox racine). Une config qui ne mentionne
pas la clé ne change pas de comportement. **Rétrocompatible.**

### 2. `OutlookEmailSource` — navigation vers le sous-dossier

Le constructeur prend un paramètre optionnel :

```python
def __init__(self, subfolder: str = "") -> None:
    import win32com.client
    outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    inbox = outlook.GetDefaultFolder(6)          # olFolderInbox
    self._folder = _resolve_subfolder(inbox, subfolder)
```

- `self._inbox` est renommé `self._folder` (ce n'est plus forcément l'inbox).
- `list_recent` : `self._folder.Items` (logique de tri/itération inchangée).
- `get` : `self._folder.Session.GetItemFromID(...)` — `Session` est disponible
  sur n'importe quel dossier, comportement identique.

### 3. Helper isolé et testable

```python
def _resolve_subfolder(inbox, subfolder: str):
    """Retourne le sous-dossier nommé (insensible à la casse) sous `inbox`,
    ou `inbox` lui-même si `subfolder` est vide. Lève RuntimeError avec la
    liste des dossiers disponibles si le nom est introuvable."""
    if not subfolder:
        return inbox
    for f in inbox.Folders:                       # Folders : collection petite et stable (≠ Items)
        if str(f.Name).casefold() == subfolder.casefold():
            return f
    available = [str(f.Name) for f in inbox.Folders]
    raise RuntimeError(
        f"Sous-dossier Outlook {subfolder!r} introuvable sous l'Inbox. "
        f"Disponibles : {available}"
    )
```

- **Matching insensible à la casse** (`casefold`) : « ligne adressage » =
  « Ligne adressage ». Les accents/apostrophes doivent correspondre au libellé
  exact (ici « ligne adressage », sans accent).
- Itérer `inbox.Folders` avec un `for` est sûr : contrairement à `Items`, la
  collection `Folders` est petite et stable.

### 4. Câblage CLI

`src/formfiller/cli.py` : `source = OutlookEmailSource()` devient
`source = OutlookEmailSource(subfolder=config.inbox_subfolder)`.

### 5. Gestion d'erreur

Dossier absent → `RuntimeError` explicite listant les sous-dossiers
disponibles, pour diagnostiquer immédiatement une faute de frappe sur le nom.

## Tests

Le COM Outlook n'est pas testable en CI (Windows + Outlook requis). On teste le
**helper pur** `_resolve_subfolder` avec un faux objet dossier
(`.Name` + `.Folders` = liste d'objets à `.Name`) :

- `tests/test_email_source.py` (nouveau) :
  - `subfolder=""` → retourne l'inbox tel quel ;
  - nom exact → retourne le bon sous-dossier ;
  - casse différente (« Ligne Adressage ») → match quand même ;
  - nom introuvable → `RuntimeError` mentionnant les noms disponibles.
- `tests/test_config.py` : défaut `inbox_subfolder == ""` + override.

## Portée des fichiers

`config.py`, `config.yaml`, `email_source.py`, `cli.py`, `tests/test_config.py`,
`tests/test_email_source.py` (nouveau). Changement additif, pas de refacto.
Branche feature `feat/inbox-subfolder`, TDD, `pytest` à la fin.
