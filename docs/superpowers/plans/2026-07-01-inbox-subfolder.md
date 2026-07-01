# Inbox Subfolder Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `OutlookEmailSource` list emails from a configurable direct subfolder of the Inbox (the "ligne adressage" folder) instead of the general Inbox.

**Architecture:** Add a `inbox_subfolder` config field (blank → Inbox root, backward compatible). Extract a pure, testable `_resolve_subfolder(inbox, name)` helper that navigates the Inbox's `Folders` collection by case-insensitive name; `OutlookEmailSource.__init__` takes a `subfolder` param and uses the helper to pick the folder it reads. The CLI passes the config value through. `get(entry_id)` is unchanged (it resolves items via `Session`, folder-agnostic).

**Tech Stack:** Python ≥3.11, Pydantic v2, pytest, Outlook desktop COM via pywin32 (`win32com.client`).

## Global Constraints

- Python ≥3.11.
- Run `pytest` after each task; all tests must pass.
- Work on branch `feat/inbox-subfolder` (already created). Never commit to `main`.
- Additive and backward compatible: default `inbox_subfolder=""` → reads the Inbox root (today's behavior).
- Folder-name matching is CASE-INSENSITIVE (`casefold`); accents/spelling must match exactly. The real folder label is `ligne adressage` (no accents).
- Do NOT change `get(entry_id)`'s use of `Session.GetItemFromID` (folder-agnostic).
- The COM paths (win32com construction, `list_recent`, `get`) are not runnable in CI — only the pure `_resolve_subfolder` helper is unit-tested; the COM wiring is verified by review.
- `config.yaml` is CARVED OUT of the implementer's scope: it holds the user's uncommitted local runtime edits. The controller commits the `inbox_subfolder: "ligne adressage"` line separately (see "Controller-owned step" below). Implementer tasks must NOT touch `config.yaml`.

---

## File Structure

- **Modify** `src/formfiller/config.py` — add `inbox_subfolder: str = ""` to `AppConfig`.
- **Modify** `src/formfiller/email_source.py` — add module-level `_resolve_subfolder`; change `OutlookEmailSource.__init__` to take `subfolder=""` and use the helper; rename `self._inbox` → `self._folder`.
- **Modify** `src/formfiller/cli.py` — construct `OutlookEmailSource(subfolder=config.inbox_subfolder)` in `main()`.
- **Modify** `tests/test_config.py` — default + override for `inbox_subfolder`.
- **Modify** `tests/test_email_source.py` — tests for `_resolve_subfolder` using a fake folder object.
- **Controller-owned:** `config.yaml` — add `inbox_subfolder: "ligne adressage"` (committed by controller, not implementer).

---

## Task 1: Config field `inbox_subfolder`

**Files:**
- Modify: `src/formfiller/config.py:17` (after the `inbox_list_count` field)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (leaf).
- Produces: `AppConfig.inbox_subfolder: str = ""`.

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_config.py`:

```python
def test_appconfig_inbox_subfolder_default_and_override():
    from formfiller.config import AppConfig
    assert AppConfig(excel_log_path="x.xlsx").inbox_subfolder == ""
    assert AppConfig(excel_log_path="x.xlsx",
                     inbox_subfolder="ligne adressage").inbox_subfolder == "ligne adressage"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_appconfig_inbox_subfolder_default_and_override -v`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'inbox_subfolder'` (or a Pydantic unexpected-keyword error on the override line).

- [ ] **Step 3: Add the field**

In `src/formfiller/config.py`, immediately after the `inbox_list_count` field (currently line 17), add:

```python
    inbox_subfolder: str = ""   # blank → Inbox root; else a direct subfolder of the Inbox to read
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py::test_appconfig_inbox_subfolder_default_and_override -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/config.py tests/test_config.py
git commit -m "feat(config): add inbox_subfolder"
```

---

## Task 2: `_resolve_subfolder` helper + wire into `OutlookEmailSource` + CLI

**Files:**
- Modify: `src/formfiller/email_source.py` (add `_resolve_subfolder`; change `OutlookEmailSource.__init__`, `list_recent`, `get` to use `self._folder`)
- Modify: `src/formfiller/cli.py` (the `source = OutlookEmailSource()` line in `main()`)
- Test: `tests/test_email_source.py`

**Interfaces:**
- Consumes: `AppConfig.inbox_subfolder` (Task 1).
- Produces: `_resolve_subfolder(inbox, subfolder: str)` — returns the matching child folder (case-insensitive) or `inbox` when `subfolder` is blank; raises `RuntimeError` (message contains the requested name and the list of available folder names) when not found. `OutlookEmailSource(subfolder: str = "")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_email_source.py`. First extend the import at the top (change the existing `from formfiller.email_source import FakeEmailSource, EmailSource` line to include the helper):

```python
from formfiller.email_source import FakeEmailSource, EmailSource, _resolve_subfolder
```

Then append these tests:

```python
class _FakeFolder:
    """Mimics an Outlook folder: a .Name and a .Folders collection of children."""
    def __init__(self, name, children=()):
        self.Name = name
        self.Folders = list(children)


def test_resolve_subfolder_blank_returns_inbox():
    inbox = _FakeFolder("Inbox", [_FakeFolder("ligne adressage")])
    assert _resolve_subfolder(inbox, "") is inbox


def test_resolve_subfolder_exact_match_returns_child():
    target = _FakeFolder("ligne adressage")
    inbox = _FakeFolder("Inbox", [_FakeFolder("Autre"), target])
    assert _resolve_subfolder(inbox, "ligne adressage") is target


def test_resolve_subfolder_is_case_insensitive():
    target = _FakeFolder("ligne adressage")
    inbox = _FakeFolder("Inbox", [target])
    assert _resolve_subfolder(inbox, "Ligne Adressage") is target


def test_resolve_subfolder_missing_raises_listing_available():
    inbox = _FakeFolder("Inbox", [_FakeFolder("Autre"), _FakeFolder("Divers")])
    with pytest.raises(RuntimeError) as exc:
        _resolve_subfolder(inbox, "ligne adressage")
    msg = str(exc.value)
    assert "ligne adressage" in msg
    assert "Autre" in msg and "Divers" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_email_source.py -k resolve_subfolder -v`
Expected: FAIL at import — `ImportError: cannot import name '_resolve_subfolder' from 'formfiller.email_source'`.

- [ ] **Step 3: Add the helper and wire the class**

In `src/formfiller/email_source.py`, add the module-level helper immediately above the `class OutlookEmailSource` line:

```python
def _resolve_subfolder(inbox, subfolder: str):
    """Return the Inbox subfolder named `subfolder` (case-insensitive), or
    `inbox` itself when `subfolder` is blank. Raises RuntimeError listing the
    available subfolders when the name is not found."""
    if not subfolder:
        return inbox
    for f in inbox.Folders:
        if str(f.Name).casefold() == subfolder.casefold():
            return f
    available = [str(f.Name) for f in inbox.Folders]
    raise RuntimeError(
        f"Outlook subfolder {subfolder!r} not found under the Inbox. "
        f"Available: {available}"
    )
```

Then change `OutlookEmailSource.__init__` from:

```python
    def __init__(self) -> None:
        import win32com.client  # lazy import; Windows + Outlook only

        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        # 6 == olFolderInbox
        self._inbox = outlook.GetDefaultFolder(6)
```

to:

```python
    def __init__(self, subfolder: str = "") -> None:
        import win32com.client  # lazy import; Windows + Outlook only

        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        inbox = outlook.GetDefaultFolder(6)  # 6 == olFolderInbox
        self._folder = _resolve_subfolder(inbox, subfolder)
```

In `list_recent`, change the first line from `items = self._inbox.Items` to:

```python
        items = self._folder.Items
```

In `get`, change `item = self._inbox.Session.GetItemFromID(entry_id)` to:

```python
            item = self._folder.Session.GetItemFromID(entry_id)
```

(There are no other `self._inbox` references — confirm with a search after editing.)

Finally, wire the CLI. In `src/formfiller/cli.py`, inside `main()`, change:

```python
    source = OutlookEmailSource()
```

to:

```python
    source = OutlookEmailSource(subfolder=config.inbox_subfolder)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_email_source.py -v`
Expected: PASS (the 4 new resolve tests + the 3 pre-existing FakeEmailSource tests).

Then confirm no stray `self._inbox` reference remains:

Run: `git grep -n "_inbox" -- src/formfiller/email_source.py`
Expected: no output (empty).

- [ ] **Step 5: Full suite + commit**

Run: `pytest`
Expected: PASS (whole suite green).

```bash
git add src/formfiller/email_source.py src/formfiller/cli.py tests/test_email_source.py
git commit -m "feat(email): read from a configurable Inbox subfolder"
```

---

## Controller-owned step: `config.yaml`

**Not an implementer task.** After Task 2 review passes, the controller adds the runtime value to `config.yaml` as an isolated commit that does NOT fold in the user's uncommitted local runtime edits (see the [[config-yaml-local-runtime-overrides]] handling used on 2026-07-01):

Add, immediately after the `inbox_list_count:` line:

```yaml
inbox_subfolder: "ligne adressage"   # sous-dossier de la Boîte de réception à lire
```

Technique: `git show HEAD:config.yaml` → add the line to that baseline copy → `git hash-object -w` + `git update-index --cacheinfo` to stage only that content → commit → then Edit the working `config.yaml` to add the same line so the user keeps their local overrides + the new knob, uncommitted.

Commit message: `feat(config): read the "ligne adressage" Inbox subfolder`

---

## Self-Review

**1. Spec coverage:**
- Config field `inbox_subfolder` (blank → Inbox root) → Task 1. ✓
- `_resolve_subfolder` helper (empty → inbox; case-insensitive; raises listing available) → Task 2. ✓
- `OutlookEmailSource(subfolder=...)` + `self._inbox`→`self._folder` rename; `list_recent`/`get` updated → Task 2. ✓
- `get` still uses `Session.GetItemFromID` → Task 2 preserves it (only the attribute name changes). ✓
- CLI passes `config.inbox_subfolder` → Task 2. ✓
- `config.yaml` value `ligne adressage` → Controller-owned step. ✓
- Tests: config default/override → Task 1; helper behaviors (empty/exact/case-insensitive/missing) → Task 2. ✓
- Non-goal (no root-account / no deep nesting) → design honored; helper only walks direct children of the Inbox. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows full code; every command has expected output. ✓

**3. Type consistency:** `AppConfig.inbox_subfolder` (str) from Task 1 is read as `config.inbox_subfolder` in Task 2's CLI wiring and passed to `OutlookEmailSource(subfolder: str = "")`, which forwards it to `_resolve_subfolder(inbox, subfolder: str)`. Names consistent across tasks (`inbox_subfolder`, `subfolder`, `_resolve_subfolder`, `self._folder`). ✓
