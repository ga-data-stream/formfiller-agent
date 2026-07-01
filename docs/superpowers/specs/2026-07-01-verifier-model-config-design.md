# Design — Modèle et effort de raisonnement dédiés au vérificateur

**Date** : 2026-07-01
**Statut** : validé (design)

## Problème

La phase de mapping sémantique fait deux appels LLM successifs dans
`map_and_verify` (`src/formfiller/field_mapper.py`) :

1. **Passe 1 — `map_fields`** : propose un mapping question → champ profil.
2. **Passe 2 — `_verify`** : vérifie et corrige la proposition.

Aujourd'hui les deux passes partagent le **même** modèle (`deployment`) et le
**même** `reasoning_effort`. On veut pouvoir faire tourner le vérificateur
(passe 2) sur un modèle distinct — typiquement un modèle plus fort qui
contre-vérifie une proposition produite par un modèle plus léger — avec
éventuellement un effort de raisonnement différent.

## Objectif

Rendre configurables, indépendamment de la passe 1, le **modèle** et le
**reasoning_effort** du vérificateur, sans casser la configuration existante.

## Non-objectifs

- Ne touche pas l'appel LLM de la boucle agent (`OpenAIResponsesAgentLLM`) :
  ce n'est pas un vérificateur.
- N'introduit pas de modèle par question ni de sélection dynamique de modèle.
- Ne modifie pas la signature de `_verify` (elle accepte déjà `deployment` et
  `reasoning_effort`).

## Conception

### 1. Configuration (`src/formfiller/config.py`)

Deux nouveaux champs sur `AppConfig`, suivant le pattern déjà en place
« vide → réutilise » (`agent_model_deployment: str = ""`) :

```python
verifier_model_deployment: str = ""          # vide → réutilise le modèle de mapping
verifier_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
                                              # None → réutilise reasoning_effort
```

Aucun champ obligatoire : une config existante qui ne mentionne pas ces clés
conserve le comportement actuel (vérificateur = même modèle et même effort que
la passe 1). **100 % rétrocompatible.**

### 2. Résolution du fallback — centralisée dans `map_and_verify`

`map_and_verify` gagne deux paramètres optionnels et résout elle-même le
fallback, pour ne pas disperser la logique entre les appelants :

```python
def map_and_verify(client, deployment, schema, profile, verify=True,
                   max_output_tokens=16000, reasoning_effort="medium",
                   verifier_deployment="", verifier_reasoning_effort=None):
    ...
    v_dep    = verifier_deployment or deployment          # "" → modèle de la passe 1
    v_effort = verifier_reasoning_effort or reasoning_effort   # None → effort de la passe 1
    verification = _verify(client, v_dep, schema, profile, proposed,
                           max_output_tokens, reasoning_effort=v_effort)
```

- `verifier_deployment` vide (`""`) → on réutilise `deployment`.
- `verifier_reasoning_effort` `None` → on réutilise `reasoning_effort`.

La passe 1 (`map_fields`) reste inchangée : elle utilise toujours `deployment`
et `reasoning_effort`.

### 3. Câblage CLI (`src/formfiller/cli.py`) — les deux chemins

Les deux sites d'appel passent les nouvelles valeurs de config telles quelles ;
le fallback est fait par `map_and_verify`.

- **Chemin déterministe** (`do_map`) : base = `config.azure_openai_deployment`.
- **Chemin agent** (`mapper`) : base = `deployment`
  (= `config.agent_model_deployment or config.azure_openai_deployment`).
  Le fallback du vérificateur pointe donc naturellement vers cette base, ce qui
  reste cohérent.

Dans les deux cas :

```python
map_and_verify(client, <base_deployment>, schema, profile,
               verify=config.mapping_verify,
               reasoning_effort=config.reasoning_effort,
               verifier_deployment=config.verifier_model_deployment,
               verifier_reasoning_effort=config.verifier_reasoning_effort)
```

### 4. `config.yaml`

Deux lignes commentées, laissées vides par défaut, à côté des autres réglages
LLM :

```yaml
verifier_model_deployment: ""    # vide → réutilise azure_openai_deployment pour la passe de vérification
verifier_reasoning_effort:       # vide → réutilise reasoning_effort pour la passe de vérification
```

## Tests

- `tests/test_config.py` : défauts (`verifier_model_deployment == ""`,
  `verifier_reasoning_effort is None`) et override explicite.
- `tests/test_field_mapper.py` :
  - `map_and_verify` route le **modèle** et l'**effort** du vérificateur vers
    `_verify` quand ils sont fournis ;
  - retombe sur `deployment` / `reasoning_effort` quand ils sont vides/`None`
    (passe 1 inchangée) ;
  - un `verifier_reasoning_effort` invalide est rejeté au niveau de la config
    (déjà couvert par le mécanisme `Literal` de Pydantic).

## Portée des fichiers

`config.py`, `field_mapper.py`, `cli.py`, `config.yaml` + tests. Changement
additif, pas de refacto. Branche feature `feat/verifier-model-config`, TDD,
`pytest` à la fin.
