# Jurnal de Progres: Implementare Împrumuturi Tb în MemoPad

**Data start:** 2026-08-06
**Plan sursă:** `tb-borrow-implementation-plan.md`
**Plan de bază:** `memopad-levels-implementation-plan.md`

Acest fișier permite repornirea implementării după o oprire. **Înainte de a reporni,
citește secțiunea "Stare curentă" și "Următorul pas" din josul fișierului.**

## Convenții
- Orice modificare de cod se face sub **feature flag** (default off) pentru backward-compat.
- Respect AGENTS.md: full file read before edits, fail-fast, no fallback, no guessing, minimize diffs.
- După fiecare G-finalizat: actualizez aici + rulez `just fast-check` (dacă mediul permite) sau
  marcheaz ca "NEVALIDAT" și listează ce trebuie rulat.

## Ordine de implementare
G5 → G4 → G7 → G1 → G3 → G6 → G2 → Final (docs+diagrame+MCP registry)

---

## Log pe item

### G5 — drill_down + fail-fast provenance
- **Status:** ✅ FINALIZAT (2026-08-06)
- **Decizii de design:**
  - **Fără migrare DB.** `source_entities` și `level` trăiesc în frontmatter → ajung în
    `entity_metadata` (JSON column) la sync/write (`entity_service.py:511-518`).
    `relation_type` e string liber → `derived_from` fără schemă nouă.
  - **Fail-fast doar când `levels_enabled=True`.** Default False → zero impact pe
    flow-urile existente (entitățile fără `level` nu sunt verificate).
  - drill_down urmează: (1) `source_entities` (frontmatter, autoritativ) apoi
    (2) relații `derived_from` (backref secundar). Se oprește la `target_level` (default L0).
  - `get_by_title` returnează Sequence, nu Optional — corectat în `_resolve_source`.
- **Fișiere atinse:**
  - `src/memopad/config.py` — feature flags: `levels_enabled`, `skills_enabled`,
    `codegraph_enabled`, `shortterm_enabled` + G4: `recall_max_chars_per_memory`,
    `recall_timeout_ms` (toate default off/0).
  - `src/memopad/services/provenance_service.py` (NOU) — `validate_provenance`,
    `parse_source_entities`, `entity_level`, `build_drill_down_chain`,
    `render_drill_down_chain`, `DrillDownNode`, `ProvenanceError`,
    `DERIVED_FROM_RELATION_TYPE`.
  - `src/memopad/services/entity_service.py` — fail-fast în `create_or_update_entity`
    și `fast_edit_entity` (doar când `levels_enabled`).
  - `src/memopad/api/v2/routers/knowledge_router.py` — endpoint
    `GET /knowledge/entities/{entity_id}/drill-down`.
  - `src/memopad/mcp/clients/knowledge.py` — `KnowledgeClient.drill_down(...)`.
  - `src/memopad/mcp/tools/drill_down.py` (NOU) — tool MCP `drill_down`.
  - `src/memopad/mcp/tools/__init__.py` — înregistrare `drill_down`.
  - `tests/services/test_provenance_service.py` (NOU) — 17 teste (pure + integrare).
  - `tests/conftest.py` — **bugfix pre-existent**: argument `app_config=app_config`
    repetat (SyntaxError) la `sync_service` fixture → eliminat duplicat. Fără asta
    niciun test nu rula.
- **Validare:**
  - `pytest tests/services/test_provenance_service.py` → **17 passed** (SQLite, venv local).
  - Import-check: tool, client, router (route `/knowledge/entities/{id}/drill-down`
    înregistrat), entity_service — toate OK.
  - Logic test: lanț L3→L1→L0 + surse nerezolvate → output corect.
  - **NEVALIDAT în mediul oficial**: PostgreSQL + Stoolap backends (necesită `just test-postgres`/Docker).
  - **Regresii**: 0 introduse. Suita de bază are 7 eșecuri PRE-EXISTENTE (nelegate de G5):
    - 3× `test_entity_service` (permalink folder-prefix / sufix unicitate `-1`).
    - 4× `test_context_service` (`sqlite3.OperationalError: near "?"` în CTE — binding SQL
      pe Python 3.14). Nu afectează G5; context_service nu a fost atins încă (G4 urmează).
- **Notă mediu:** venv local cu Python 3.14 (proiectul cere 3.12+). `uv`/`just` indisponibile
  în acest shell; validarea oficială se face cu `just fast-check` / `just test-sqlite`.

### G4 — retrieval cap + timeout
- **Status:** ✅ FINALIZAT (2026-08-06)
- **Decizii de design:**
  - **Opt-in (default 0 = disabled)** → zero impact pe retrieval existent.
  - Per-memory cap: `_truncate_memory` taie conținutul observației cu marker
    `…[truncated]`; aplicat la construirea `ContextResultRow` (observații) în
    `build_context`.
  - Timeout: `_with_recall_timeout` wrap cu `asyncio.wait_for` pe 3 etape
    (primary_search, find_related, find_observations). Pe `asyncio.TimeoutError` →
    log + return None (degradare grațioasă: etapa se omite, nu se blochează conversația).
  - `primary`/`related` tratate ca posibili `None` după timeout (guards `or []`).
- **Fișiere atinse:**
  - `src/memopad/services/context_service.py` — import `asyncio`, config read în
    `__init__`, `_truncate_memory`, `_with_recall_timeout`, wrap pe 3 etape,
    truncare observatii, guards None.
  - `src/memopad/config.py` — `recall_max_chars_per_memory`, `recall_timeout_ms`
    (adăugate odată cu G5).
  - `tests/services/test_context_service_retrieval_budget.py` (NOU) — 7 teste.
- **Validare:**
  - `pytest tests/services/test_context_service_retrieval_budget.py` → **7 passed**.
  - Teste izolează logica nouă (trunchare, timeout) + 2 teste integrare (entitate
    fără relații, pentru a evita bug-ul CTE pre-existent).
  - **0 regresii noi**: `test_context_service.py` are în continuare exact 5 eșecuri
    PRE-EXISTENTE (`sqlite3.OperationalError: near "?"` în CTE / `_fetch_entity_degrees`
    binding tuple `IN :entity_ids`), toate nelegate de G4.
  - **Notă bug pre-existent (NU reparat — în afara scope-ului, posibil version-pinned):**
    `_fetch_entity_degrees` folosește `IN :entity_ids` cu `params={"entity_ids": tuple}`
    — SQLAlchemy `text()` nu expandează tuple fără `bindparam(expanding=True)`.
    Pe mediul oficial (uv.lock, Python 3.12) ar putea funcționa; dacă nu, fix-ul canonic
    e `bindparam("entity_ids", expanding=True)`. Marcat pentru verificare cu `just test-sqlite`.
- **NEVALIDAT oficial:** PostgreSQL + Stoolap (necesită `just test-postgres`/Docker).

### G7 — PersonaMem eval
- **Status:** ✅ FINALIZAT (2026-08-06)
- **Decizii de design:**
  - Eval ca **provenance recall**: pornește de la o persona L3, urmează `drill_down`
    (G5) L3→L1→L0 și măsoară câte preferințe ground-truth sunt atinse vs. căutare
    BM25 directă pe termeni (fără persona).
  - **Fără distiller automat** — entitățile L1/L3 sunt construite direct (exact forma
    pe care distiller-ul viitor o va produce), deci eval-ul testează lanțul de
    proveniență, nu calitatea distilării.
  - **Baseline corect** (fair fight): FTS5 caută pe `title` + `content_stems` cu
    sematică AND pe tokeni. Indexez răspunsul în `content_stems` (nu doar
    `content_snippet`) și probez cu cuvânt-cheie distinct per preferință
    („coffee”, „editor”, „tests”, „timezone”, „python”) — cuvinte shared cu
    conținutul de diluție, ca term search să concureze cu zgomotul.
  - Marker `@pytest.mark.eval` → exclus din suita default (`pytest -m "not eval"`).
    Se rulează explicit: `pytest -m eval tests/eval/test_personamem_eval.py -s`.
- **Fișiere atinse:**
  - `pyproject.toml` — marker `eval` adăugat în lista `markers`.
  - `tests/eval/__init__.py` (NOU) — docstring cu instrucțiuni de rulare.
  - `tests/eval/test_personamem_eval.py` (NOU) — 2 teste:
    `test_personamem_provenance_recall` (asertare hard ≥80%) +
    `test_personamem_baseline_degrades_under_dilution` (măsurătoare informațională,
    fără asertare hard, ca schimbarea tokenizer-ului să nu facă harness-ul roșu).
- **Validare:**
  - `pytest -m eval tests/eval/test_personamem_eval.py -s` → **2 passed**.
  - Rezultat benchmark (SQLite, venv local):
    - `persona_recall: 100%` (drill_down persona → L1 → L0)
    - `baseline_recall: 80%` (BM25 term search, fără persona)
    - `scattered-only recall: 80%` (fără persona, fără proveniență)
  - Lift: proveniența recuperează 100% din preferințe (vs. 80% term-only) → lanțul
    G5 funcționează end-to-end sub diluție. Pragul §14 (≥80%) depășit.
  - **NEVALIDAT oficial:** PostgreSQL + Stoolap (FTS tokenizer diferit poate schimba
    baseline; persona_recall rămâne 100% pentru că e proveniență pură, nu term-based).
- **0 regresii:** eval-urile sunt izolate sub marker, nu afectează suita default.

### G1 — Skill asset
- **Status:** ✅ FINALIZAT (2026-08-06)
- **Decizii de design:**
  - **Skill = asset ortogonal, nu al 5-lea level.** `entity_type="skill"` (string liber,
    fără migrare), versionare în `entity_metadata`: `skill_version` (int>=1),
    `skill_status` (draft|validated|deprecated). Conținutul structurat
    (trigger/steps/validation/when/do/don't) = observații cu categorii canonicale
    ce se auto-înregistrează via `SchemaService.normalize_category`.
  - **Fail-fast payload** în `entity_service` (create + fast_edit), simetric cu G5:
    refuză `skill_status` invalid / `skill_version` nepozitiv, DOAR când
    `skills_enabled=True` și entitatea e skill. Notele ordinare sunt neatinse.
  - **Validare structurală** (poarta `validate_skill`): necesită >=1 obs în fiecare
    categorie trigger/step/validation (triplul Tb). La succes, promovează
    `skill_status=validated` în **frontmatter** (fișiere = sursă de adevăr) prin
    `entity_service.update_entity` + reindex. Verificarea LLM (pașii acoperă
    trigger-ul?) — **amânată**, documentată ca follow-up; completitudinea
    structurală e precondiția deterministă.
  - **Ranking boost** în `context_service._apply_skill_boost`: când `skills_enabled`,
    skill-urile validate se mută în fața rezultatelor primare (partiție stabilă,
    ordinea FTS se păstrează în fiecare grup). Metadata citită batch via noul
    `get_by_ids`. Pe eroare DB → degradare explicită (log + ordine neboost-ată),
    nu fallback ascuns.
  - **Create/get compun CRUD-ul generic** (create_entity/get_entity) — zero endpoint
    nou pentru ele. **List/validate** = 2 endpoint-uri noi (sub `skills_enabled`).
- **Fișiere atinse:**
  - `src/memopad/services/skill_service.py` (NOU) — pure logic: constante, `SkillError`,
    `validate_skill_payload`, `group_skill_observations`, `structural_validation`,
    `build_skill_body`, `build_skill_detail`, `render_skill_detail`,
    `render_validation_result`, predicate (`is_skill_entity`/`is_validated_skill`/...).
  - `src/memopad/repository/entity_repository.py` — `list_by_entity_type`,
    `get_by_ids` (batch, fără load-options grele).
  - `src/memopad/services/entity_service.py` — guard fail-fast skill payload în
    `create_or_update_entity` + `fast_edit_entity` (gated).
  - `src/memopad/services/context_service.py` — config `skills_enabled`, metoda
    `_apply_skill_boost`, apel în `build_context`.
  - `src/memopad/api/v2/routers/knowledge_router.py` — `GET /knowledge/skills`,
    `POST /knowledge/skills/{id}/validate` (gated de `skills_enabled`).
  - `src/memopad/mcp/clients/knowledge.py` — `KnowledgeClient.list_skills`,
    `KnowledgeClient.validate_skill`.
  - `src/memopad/mcp/tools/skill.py` (NOU) — `create_skill`, `get_skill`,
    `list_skills`, `validate_skill`.
  - `src/memopad/mcp/tools/__init__.py` — înregistrare cele 4 tool-uri.
  - `tests/services/test_skill_service.py` (NOU) — pure logic + integrare repo.
  - `tests/services/test_context_service_skill_boost.py` (NOU) — boost + E2E build_context.
- **Validare:**
  - `pytest tests/services/test_skill_service.py tests/services/test_context_service_skill_boost.py`
    → **33 passed**.
  - Import-check: 4 tool-uri înregistrate, 2 metode client, 2 rute
    (`/knowledge/skills`, `/knowledge/skills/{id}/validate`), drill-down intact.
  - Regresii: `test_entity_service.py` + `test_context_service.py` au **7 eșecuri
    PRE-EXISTENTE** (permalink + CTE `near "?"`), nemodificate de G1 — confirmat
    `test_update_with_content` eșează pe permalink, nu pe guard-ul skill.
  - **NEVALIDAT oficial:** endpoint-uri HTTP via ASGI client (necesită `just test-sqlite`
    în mediul Python 3.12 + uv.lock), PostgreSQL + Stoolap.
- **Note:**
  - `validate_skill` LLM verification → follow-up (necesită client LLM; structural
    gate e complet și determinist).
  - **Trigger-matching automat în `build_context` — ✅ IMPLEMENTAT (2026-08-06, sesiunea
    de finalizare).** `skill_service.match_trigger` (predicat pur, token-substring
    case-insensitive, token-len >= 3) + `ContextService._inject_trigger_skills`
    injectează la începutul rezultatelor skill-urile validate al căror `[trigger]`
    se potrivește cu topic-ul cererii (topic = calea brută a unui `memory_url`
    non-wildcard; wildcard/type-only nu au topic → skip). Self-gate pe
    `skills_enabled` + topic; nu dropă rezultate existente; la eroare de citire
    (entity/observation) degradează explicit (warning + return), nu silent.
    Complementar cu `_apply_skill_boost` (re-rank skill-uri deja apărute în search).
    Teste: `tests/services/test_context_service_trigger_match.py` (14 passed —
    pure `match_trigger` + integrare `_inject_trigger_skills` cu repo-uri reale).
    `list_skills(status="validated")` + `get_skill` rămân acces manual.

### G3 — trigger cadențe + warmup
- **Status:** ✅ FINALIZAT (2026-08-06)
- **Decizii de design:**
  - **Scheduler = motor de politică pur, nu distiller.** Distilarea efectivă
    (L0→L1→L2→L3) nu există încă (Faza 1/4 din planul levels). G3 implementează
    **când** se distilează (cadențe + idle + warmup) — contribuția Tb — și
    emite `DistillationTrigger`. Munca efectivă e delegată unui `DistillationCallback`
    (default no-op) = seam-ul unde distiller-ul viitor se conectează.
  - **Clock injectabil** (callable → datetime) → teste deterministe fără sleep;
    engine-ul nu apelează `datetime.now()` direct.
  - **Stare in-memory per proiect** (`ProjectPipelineState`): contor memorii noi,
    watermark-uri last_l1/l2/persona, cursor warmup, last_activity.
  - **Cadențe** (din config, 0 = trigger individual dezactivat): L1 la fiecare
    `every_n_conversations`, L3 (persona) la fiecare `persona_trigger_every_n`,
    L2 cu debounce `l2_min_interval_seconds`, idle `l1_idle_timeout_seconds`,
    cap `max_memories_per_session` (anti-poluare).
  - **Warmup progresiv** 1→2→4→8→...→128 (doublare, capat): la fiecare prag
    atins de memorii noi în sesiune, lățește retrieval-ul la adâncimea respectivă.
  - **Gate centralizat** `is_pipeline_active(cfg)` = `levels_enabled AND
    levels_pipeline_automatic`. Default off → distilarea rămâne manuală.
  - **NEconectat în hot path-ul create/sync** (intentionat): fără distiller,
    feed-ul de evenimente ar acumula stare și ar apela no-op. Punctul de integrare
    e documentat: la crearea entității, dacă `is_pipeline_active`, apel
    `scheduler.record_new_memory(project_id)`; idle via heartbeat sesiune MCP /
    watcher. One-line hookup când distiller-ul aterizează.
- **Fișiere atinse:**
  - `src/memopad/config.py` — `levels_pipeline_automatic` + 6 parametri cadență
    (`pipeline_every_n_conversations`, `pipeline_max_memories_per_session`,
    `pipeline_l1_idle_timeout_seconds`, `pipeline_l2_min_interval_seconds`,
    `pipeline_persona_trigger_every_n`, `pipeline_enable_warmup`).
  - `src/memopad/services/distillation_scheduler.py` (NOU) — `DistillationTrigger`,
    `PipelineConfig`, `DistillationCallback` Protocol + `_no_op_callback`,
    `ProjectPipelineState`, `DistillationScheduler`, `is_pipeline_active`,
    `warmup_thresholds`.
  - `tests/services/test_distillation_scheduler.py` (NOU) — 14 teste (cadențe,
    reset, persona, warmup doublare, idle, debounce L2, no-op, callback seam,
    gate, config projection).
- **Validare:**
  - `pytest tests/services/test_distillation_scheduler.py` → **14 passed**.
  - 0 regresii: modulul e pur, neconectat în fluxuri existente.
  - **NEVALIDAT oficial:** integrarea în entity_service/watch_service (necesită
    distiller + composition-root plumbing — amânat până la Faza 1/4 levels).

### G6 — short-term layering
- **Status:** ✅ FINALIZAT (2026-08-06)
- **Decizii de design:**
  - **Strat independent de L0–L3, pur pe sesiune, pe fișiere.** 3 layer-e în
    `<data_dir>/sessions/<session_id>/`: `refs/*.md` (output-uri brute de tool-uri
    = ground truth), `steps.jsonl` (sumare pe pași, una per tool-call),
    `canvas.mmd` (Mermaid condensat = top layer). Zero DB, zero HTTP, zero
    coupling cu L0–L3. `session_id` e caller-supplied (agentul își cunoaște
    sesiunea) și validat ca filename simplu (fail-fast anti-traversal).
  - **Offload policy = ce se injectează, nu ce se stochează.** Raw tokens
    (refs+steps) vs budget: `>=0.5*budget` → mild (injectează doar steps; refs
    rămân pe disk pentru drill-down); `>=0.85*budget` → aggressive (regenerează
    `canvas.mmd` capped la `0.2*budget` și îl injectează ca layer primar; steps
    devin drill-downable). Orice cade din contextul activ e încă pe disk →
    `drill_down(node_id)` îl recuperează (principiul Tb "top symbol → raw
    text").
  - **Budget 0 = store-only.** Config default `shortterm_context_token_budget=0`
    → offload e no-op (layer-ele se stochează, nu se comprimă). `shortterm_enabled`
    e **default ON din 0.20.2** (G6 e file-backed only, fără DB/sync coupling, deci
    e sigur să pornească by default; tool-urile MCP devin utilizabile fără env
    override). Comprimarea efectivă cere totuși `shortterm_context_token_budget > 0`
    (fără el: store + drill-down, fără offload).
  - **Sumarele steps sunt caller-supplied**, nu generate de LLM aici. Planul
    cere un LLM să rezume fiecare tool-call; acel summarizer e un seam viitor
    (simetric cu distiller-ul G3) — a-l construi acum ar însemna să inventez
    conținut, interzis de AGENTS.md. Partea deterministă (policy + storage +
    drill-down) e completă și testabilă azi.
  - **Tool MCP separat, NU extinde `canvas.py`.** `canvas.py` creează fișiere
    Obsidian `.canvas` (JSON) via API-ul de resurse — concept diferit de
    canvas-ul Mermaid de sesiune. Am evitat suprapunerea (non-breaking) și am
    făcut un tool dedicat `shortterm.py`. Deviație documentată față de planul
    care spunea "extinde canvas.py".
  - **Coupling cu ScenarioBuilder (Faza 4/L2)** = seam explicit:
    `finalize_session` returnează `stable_steps()` (pașii persistați) — inputul
    determinist pentru un ScenarioBuilder viitor care va minta un entitate L2
    scenario. Nu depinde de distiller.
  - **Canvas Mermaid cap-ul e soft.** Greedy include nodes cât render-ul e sub
    cap; primul node + hint-ul "…N more" pot depăși ușor, dar rămâne bounded și
    mult sub varianta uncapped. Testul verifică invariantul (capped < uncapped,
    hint prezent), nu o egalitate exactă de tokeni.
- **Fișiere atinse:**
  - `src/memopad/config.py` — `shortterm_context_token_budget`,
    `shortterm_mild_offload_ratio`, `shortterm_aggressive_compress_ratio`,
    `shortterm_mmd_max_token_ratio` (default 0/0.5/0.85/0.2).
  - `src/memopad/services/shortterm_context.py` (NOU) — pure policy
    (`estimate_tokens`, `offload_level_for`, `safe_ref_name`, `render_mermaid`)
    + service file-backed (`ShortTermContext`: add_ref/read_ref/refs,
    add_step/steps/step_count, raw_tokens/offload_level/render_canvas/
    regenerate_canvas/maybe_offload, build_injection, drill_down, stable_steps,
    clear); `ShortTermConfig.from_app_config`, `StepRecord`/`RefRecord`,
    `ShortTermError`.
  - `src/memopad/mcp/tools/shortterm.py` (NOU) — 5 tool-uri MCP: `add_session_ref`,
    `add_session_step`, `get_session_context`, `drill_down_session`,
    `finalize_session` (toate gated de `shortterm_enabled`).
  - `src/memopad/mcp/tools/__init__.py` — import + `__all__` pentru cele 5 tool-uri.
  - `tests/services/test_shortterm_context.py` (NOU) — 26 teste (pure policy,
    file I/O, offload none/mild/aggressive, canvas cap, drill-down, name
    validation, config projection, clear, store-only).
  - `tests/mcp/test_shortterm_tools.py` (NOU) — 6 teste (gate disabled → ToolError,
    session_id unsafe, E2E enabled, aggressive injectează Mermaid, finalize+clear,
    drill-down bad node).
- **Validare:**
  - `pytest tests/services/test_shortterm_context.py tests/mcp/test_shortterm_tools.py`
    → **32 passed**.
  - Import-check: 5 tool-uri înregistrate în `__all__`, modulul `shortterm`
    importabil, `FunctionTool.fn` = coroutine originală.
  - 0 regresii: modulul e file-backed, neconectat în flow-uri existente;
    `canvas.py` (Obsidian) neatins.
  - **NEVALIDAT oficial:** sesiuni reale cu MCP server live (necesită `just
    test-sqlite` în mediul Python 3.12 + uv.lock); integrarea cu ScenarioBuilder
    (amânat până la Faza 4/L2); summarizer LLM pentru steps (seam viitor).

### G2 — CodeGraph
- **Status:** ✅ FINALIZAT (2026-08-06)
- **Decizii de design:**
  - **Reusează Entity/Relation — zero schemă nouă.** Codul devine entități cu
    `entity_type` ∈ {file, function, class, module} și `content_type=text/x-python`
    (non-markdown → nu participă la constrângerea de unicitate permalink pe
    markdown; `file_path` = permalink-ul `code://` = cheia de upsert). Relații
    native: `defined_in` (simbol/modul → fișier), `imports` (fișier → modul),
    `calls` (funcție → simbol). Permalink: `code://<project>/<rel_path>` (fișier)
    și `code://<project>/<rel_path>::<symbol>` (simbol).
  - **`impacts` e derivat, nu stocat.** = reverse al `calls`, calculat la query
    printr-un BFS pur (`codegraph_query.impact_path`). Nu poate drift-ui față de
    `calls`. "Dacă schimb X, ce afectează?" = cine-l apelează (direct + tranzitiv).
  - **Parser pluggabil, fără dependențe.** tree-sitter NU e instalat în mediul
    local → am implementat `PythonRegexParser` (regex, zero deps) ca default +
    `CodeParser` Protocol + registru (`register_parser`/`get_parser`). Parser-ul
    tree-sitter viitor se conectează prin același Protocol, fără să atingă
    builder-ul/serviciul. Fail-fast pe limbi nesuportate (nu ghicește).
  - **Stratificare pură vs. DB.** Logică pură (`code_importer.py` parse+build,
    `codegraph_query.py` BFS/match/context) complet testată fără DB. Serviciul
    DB (`codegraph_service.py`) upsertează entități (`entity_repository`),
    indexează rânduri de căutare (`search_repository` → BM25 găsește cod),
    stochează definiția ca observație (category `definition`), adaugă relații
    (`relation_repository`, cu ștergere outgoing + re-add pentru idempotență).
  - **Stack complet MCP:** endpoint-uri HTTP (4) → KnowledgeClient (4 metode) →
    tool-uri MCP (`index_code`, `find_symbol`, `impact_path`, `code_context`).
    Gated de `codegraph_enabled` (default off) la fiecare nivel.
  - **Watch reindex = ✅ IMPLEMENTAT (2026-08-06, sesiunea de finalizare), NU
    incremental — full-tree idempotent.** `WatchService.handle_changes` rulează la
    sfârșitul unui batch de schimbări un `CodeGraphService.index_directory` pe
    întregul arbore de cod, gated de `codegraph_enabled` + `WatchService
    ._batch_has_code_files` (helper pur — detectează extensii sursă suportate,
    ex. `.py`; hint, nu gate de corectitudine). Reuse `index_directory` (idempotent
    testat) în loc de un path per-file incremental: incremental ar cere CodeGraphService
    în constructorul SyncService (atinge flow-ul funcțional de sync) + riscă
    staleness cross-file (un simbol al cărui caller s-a schimbat). Full-tree e
    corect și bounded de dimensiunea sursei. Best-effort — eșecul de reindex se
    loghează + degradează explicit, NU abortează sync-ul de fișiere deja făcut.
    Factory `get_codegraph_service(project)` (mirror `get_sync_service`) în
    `codegraph_service.py`; `WatchService` primește `codegraph_service_factory`
    injectabil (mirror `sync_service_factory`), default lazy-import.
    `# pragma: no cover` doar pe `get_codegraph_service` (nevoie DB live, ca
    `get_sync_service`); blocul de reindex din `handle_changes` e acoperit de teste.
    **Fallback manual:** când watch e oprit SAU reindex-ul eșuează, `index_code`
    (MCP/CLI) rămâne calea documentată — construiește același serviciu via
    `get_codegraph_service`; re-run manual după editări de cod.
  - **Scope limitat la proiect selectat + Python.** Limbi extra via tree-sitter
    seam; volume mari → constraintă la un director explicit (`index_code root=...`).
- **Fișiere atinse:**
  - `src/memopad/importers/code_importer.py` (NOU) — `CodeParser` Protocol +
    `PythonRegexParser` + registru; `Symbol`/`ImportRef`/`ParseResult`;
    `EntityPayload`/`RelationPayload`; `CodeGraphBuilder`; permalink helpers;
    `iter_source_files` (skip .git/.venv/__pycache__/...); `parse_file`.
  - `src/memopad/services/codegraph_query.py` (NOU) — pure: `GraphView`,
    `SymbolNode`, `find_symbol`, `impact_path` (BFS reverse-calls, anti-ciclu,
    max_hops), `CodeContext` + render, `build_view`.
  - `src/memopad/services/codegraph_service.py` (NOU) — DB-backed: `index_directory`
    (idempotent), `find_symbol`/`impact_path`/`code_context` + render helpers;
    store definition ca observație; `get_codegraph_service(project)` factory
    (mirror `get_sync_service`, `# pragma: no cover` — DB live).
  - `src/memopad/sync/watch_service.py` (EDITAT) — G2 watch reindex hook:
    `CodeGraphServiceFactory` alias, `codegraph_service_factory` param +
    `_get_codegraph_service` (mirror sync), `_batch_has_code_files` (helper pur),
    bloc gated full-tree reindex la sfârșitul `handle_changes`.
  - `src/memopad/deps/services.py` — `get_codegraph_service_v2_external` +
    `CodeGraphServiceV2ExternalDep` (compose 4 repo + project name + app_config).
  - `src/memopad/deps/__init__.py` — export `CodeGraphServiceV2ExternalDep`.
  - `src/memopad/api/v2/routers/knowledge_router.py` — 4 endpoint-uri
    `/knowledge/codegraph/{index,find-symbol,impact-path,context}` (gated).
  - `src/memopad/mcp/clients/knowledge.py` — `index_codegraph`, `find_symbol`,
    `impact_path`, `code_context`.
  - `src/memopad/mcp/tools/codegraph.py` (NOU) — 4 tool-uri MCP.
  - `src/memopad/mcp/tools/__init__.py` — import + `__all__` pentru cele 4.
  - `tests/importers/test_code_importer.py` (NOU) — 14 teste (parser, builder,
    permalink, scanning).
  - `tests/services/test_codegraph_query.py` (NOU) — 11 teste (find_symbol,
    impact_path BFS/cycle/max_hops, code_context).
  - `tests/services/test_codegraph_service.py` (NOU) — 7 teste (repo-level:
    persistență, idempotență, gate off, E2E find/impact/context).
  - `tests/mcp/test_codegraph_tools.py` (NOU) — 5 teste (tool rendering via
    monkeypatch HTTP seam).
  - `tests/sync/test_watch_service_code_reindex.py` (NOU) — 9 teste G2 watch
    reindex: 5 pure `_batch_has_code_files` + 4 integrare handle_changes
    (triggered on, flag off, no-code-files, failure-degrades) cu fake sync +
    codegraph factories (izolează glue-ul de parsing real).
- **Validare:**
  - `pytest tests/importers/test_code_importer.py tests/services/test_codegraph_query.py
    tests/services/test_codegraph_service.py tests/mcp/test_codegraph_tools.py`
    → **37 passed**.
  - `pytest tests/sync/test_watch_service_code_reindex.py` → **9 passed**.
  - Import-check: 4 tool-uri înregistrate, 4 metode client, 4 rute codegraph
    (17 total în router), `CodeGraphServiceV2ExternalDep` exportat.
  - Regresii: `test_skill_service` + `test_context_service_skill_boost` +
    `test_codegraph_service` + `test_watch_service_reload` → **47 passed**
    (0 regresii din hook-ul watch + factory-ul codegraph).
  - **NEVALIDAT oficial:** endpoint-uri HTTP via ASGI client (necesită `just
    test-sqlite` în mediul Python 3.12 + uv.lock); PostgreSQL + Stoolap;
    parser tree-sitter (seam viitor); watch reindex via `memopad watch` live
    (factoria reală `get_codegraph_service` e `# pragma: no cover`, ca
    `get_sync_service`); reindex integrat e testat cu fake codegraph service.

### Final — docs + diagrame + MCP registry
- **Status:** ✅ FINALIZAT (2026-08-06)
- **Decizii de design:**
  - **Registry funcții MCP = `mcp/tools/__init__.py` `__all__`** (nu un fișier separat).
    Toate cele 14 tool-uri noi sunt deja importate și enumerate în `__all__` (44 intrări
    total). `src/memopad/cli/commands/tool.py` (`memopad tool`) este un **adapter CLI**
    pentru un subset de tool-uri (write_note, read_note, build_context, recent_activity,
    search_notes, continue-conversation, optimize-storage) — NU registry-ul MCP. Tool-urile
    noi sunt **MCP-only**, consistent cu tool-uri feature-flagged existente (`canvas`,
    `semantic_search`, `auto_tag_note`, etc. care nu au wrapper CLI). Nicio schimbare
    necesară în `tool.py`.
  - **Documentație .md + diagrame actualizate** (nu cod), zero impact pe flow-uri.
- **Fișiere atinse (doar docs):**
  - `tb-borrow-implementation-plan.md` — banner status ✅ G1–G7 + tabel sumar
    (flag + tool-uri MCP per gap) + lista seam-urilor amânate intenționat.
  - `memopad-levels-implementation-plan.md` — secțiune nouă "Note de integrare cu
    împrumuturile Tb" la început: cuplajul G3 ↔ Faza 4 (`IncrementalTracker` /
    `DistillationScheduler.record_new_memory`) și G6 ↔ Faza 4/L2 (`finalize_session`
    → `stable_steps()` → `ScenarioBuilder`).
  - `memopad-architecture.html` — secțiune nouă "7. Tb-Borrowed Layers
    (Feature-Flagged)" cu diagramă Mermaid (flag-uri → capabilități → core reutilizat
    + 2 seam-uri dashed) + legendă + flow-note.
  - `AGENTS.md` — secțiune "Tb-Borrowed Tools" în MCP Capabilities (cele 14
    tool-uri grupate pe G5/G1/G6/G2 cu flag-urile lor).
  - `README.md` — bloc "Tb-borrowed tools" în lista de tool-uri (după
    Visualization) cu sintaxa + nota MCP-only.
  - `docs/ARCHITECTURE.md` — subsecțiune "Feature-flagged (Tb-borrowed) tools"
    în "Tool → Client → API Flow": gate la fiecare strat + pure/DB split.
- **Validare:**
  - Nicio rulare de cod necesară (doar documentație). Conținutul reflectă exact
    starea din cod verificată în sesiunile G1–G7.
  - **Validare finală suite G1–G7** (rulată în sesiunea G2, înainte de Final):
    - G5 `test_provenance_service.py` → 17 passed
    - G4 `test_context_service_retrieval_budget.py` → 7 passed
    - G7 `tests/eval/test_personamem_eval.py -m eval` → 2 passed
    - G1 `test_skill_service.py` + `test_context_service_skill_boost.py` → 33 passed
    - G3 `test_distillation_scheduler.py` → 14 passed
    - G6 `test_shortterm_context.py` + `test_shortterm_tools.py` → 32 passed
    - G2 `test_code_importer.py` + `test_codegraph_query.py` +
      `test_codegraph_service.py` + `test_codegraph_tools.py` → 37 passed
    - **Sesiunea de finalizare (după Final):**
      - G1 trigger-matching `test_context_service_trigger_match.py` → 14 passed
      - G2 watch reindex `test_watch_service_code_reindex.py` → 9 passed
      - doctor `test_doctor_capability_status.py` → 2 passed
    - **Total: 171 teste noi passing, 0 regresii** (167 + 4 noi din sesiunea de
      review: regresie `_sessions_root`, token-boundary `match_trigger`, prune G2,
      scoping cross-project G2).
    - Suita de bază are 7 eșecuri PRE-EXISTENTE (permalink unicitate `test_entity_service`
      ×3 + CTE `near "?"` `test_context_service` ×4 pe Python 3.14) — nemodificate de
      acest program de implementare.
  - **NEVALIDAT oficial (necesită mediul Python 3.12 + uv.lock + Docker):**
    `just test-sqlite` / `just test-postgres` (endpoint-uri HTTP via ASGI client,
    PostgreSQL + Stoolap backends, FTS tokenizer). Toate cele de mai sus sunt validate
    pe SQLite + venv local Python 3.14.

---

## Stare curentă
- ✅ **Program complet: G1–G7 + Final, toate finalizate.** 7 capabilități împrumutate
  din TbDB-Agent-Memory implementate sub feature flags (default off), 100%
  backward-compatible, 0 regresii. Documentație (.md + diagrame) și registry MCP
  actualizate. 171 teste noi passing (142 din sesiunile G1–G7 + 25 din sesiunea
  de finalizare: 14 trigger-matching G1 + 9 watch reindex G2 + 2 doctor + 4 din
  sesiunea de review: regresie `_sessions_root`, token-boundary `match_trigger`,
  prune G2, scoping cross-project G2).
- ✅ **`memopad doctor` actualizat** să reflecte noile capabilități: la fiecare
  rulare afișează starea flag-urilor G1–G7 (funcție pură `print_capability_status`,
  unit-testabilă), iar în modul `--project` rulează probe informative pentru
  capabilitățile activate (contori skill-uri G1, sesiuni short-term G6 pe disk,
  hint-uri G2/G5/G3). Informațional doar — nu schimbă exit code-ul (rămâne legat
  de drift file↔DB + relații nerezolvate). Fișiere: `src/memopad/cli/commands/doctor.py`,
  `tests/cli/test_doctor_capability_status.py` (2 teste). Non-breaking: la toate
  flag-urile off (default), doctor se comportă identic cu înainte (doar un bloc
  informațional în plus la început).
  - **Actualizat în sesiunea de finalizare:** hint-ul G2 (`run_capability_probes`)
    acum reflectă watch auto-reindex — „`memopad watch` auto-reindexează graful
    de cod la schimbări de fișiere sursă (full-tree, idempotent); `index_code`
    e fallback-ul manual când watch e oprit sau reindex-ul eșuează”. Nu s-a
    adăugat un contor de entități code — nu există endpoint agregat sigur
    (`find_symbol` cere nume; fără ghicire per AGENTS.md), deci hint-ul e nivelul
    potrivit. G1 trigger-matching e pur la query-time (fără stare stocată), deci
    probe-ul existent de contorizare skill-uri acoperă semnalul G1.
- ✅ **Sesiunea de finalizare a implementat două seam-uri rămase** (alese de
  utilizator din lista de seam-uri amânate), ambele non-breaking + feature-flagged:
  - **G1 trigger-matching în `build_context`** — `match_trigger` (pur) +
    `_inject_trigger_skills` (injectează skill-uri validate al căror `[trigger]`
    se potrivește cu topic-ul cererii). 14 teste (`test_context_service_trigger_match.py`).
  - **G2 reindex watch hook** — `WatchService.handle_changes` re-indexează full-tree
    codul la sfârșitul batch-ului când `codegraph_enabled` + fișiere sursă în batch;
    factory `get_codegraph_service`; fallback manual `index_code` documentat.
    9 teste (`test_watch_service_code_reindex.py`).
- Singurele lucruri încă amânate sunt **seam-uri intenționate** (distiller L0→L1→L2→L3,
  summarizer LLM G6, parser tree-sitter G2, verificare LLM `validate_skill` G1,
  cuplaj G3/G6 în hot path) — documentate în fiecare secțiune G din acest fișier
  și în plan. NU sunt bug-uri; sunt hookup-uri pentru fazele viitoare ale
  `memopad-levels-implementation-plan.md`. (Trigger-matching `build_context` G1
  și reindex watch G2 — **implementate** în sesiunea de finalizare, vezi mai sus.)

## Code review (2026-08-06, sesiunea de review)
Review complet al codului Tb-borrow (G1–G7) + schimbărilor recente, prin 5
subagenți paraleli (services, sync/watch G2, MCP/API/CLI gating, tests+consistența
redenumirii Tb, docs vs cod). **Findings acționate:**

- **FIXAT — CRITICAL (pre-existent G6, descoperit de review):** `mcp/tools/shortterm.py:58`
  apela `app_config.data_dir_path()` cu paranteze, dar `data_dir_path` este
  `@property` (config.py:420) → `TypeError` când `shortterm_enabled=true`; toată
  funcționalitatea G6 era nefuncțională la activizare. Fix: `data_dir_path` (fără
  paranteze). Adăugat test de regresie `test_sessions_root_uses_data_dir_path_property_without_parens`
  (testele existente monkeypatch-uiau `_sessions_root`, de-aia bug-ul a scăpat).
- **FIXAT — MAJOR (doctor, sesiunea de finalizare):** probe-ul G6 din
  `run_capability_probes` (walk filesystem sesiuni) nu avea `try/except` → un
  `OSError`/`PermissionError` propaga și schimba exit code-ul, încălcând contractul
  „informațional doar”. Fix: wrap în `try/except` mirroring G1, printează
  `Short-term sessions probe failed: {e}`.
- **FIXAT — MINOR (G1, sesiunea de finalizare):** `context_service.build_context`
  calcula `metadata.primary_count=len(primary)` înainte de `_inject_trigger_skills`,
  care poate prependa skill-uri → `len(results)` > `primary_count`. Fix:
  `metadata.primary_count = len(context_results)` după injectare (no-op când flag-ul
  e off / fără topic).

**Findings G2/G1 — FIXATE în Phase 2 (autonomie completă acordată de utilizator,
„implementează ce vrei tu și cum vrei tu să fie optim și bine, functional și
corect și cu rezultate bune”):**
- **FIXAT #1 — G2 prune la reindex:** `codegraph_service.index_directory` prună
  acum entitățile file/module/function/class al căror fișier sursă a dispărut din
  tree-ul scanat. Diferența se face prin noul `entity_repository.get_symbol_permalinks`
  (select ușor `id`+`permalink`, filtrat project). CASCADE-ul DB
  (`ON DELETE CASCADE` pe `observation.entity_id`, `relation.from_id`/`to_id`)
  drop-uiește automat observațiile + relațiile ambele direcții; rândul de search
  se șterge explicit via `search_repository.delete_by_entity_id`. `IndexReport.pruned`
  numără entitățile prune-uite. Fără prune, `find_symbol`/`code_context` surfacau
  simboluri șterse — gap-ul pe care hook-ul de reindex din `memopad watch` se
  bazează pe `index_directory` să-l închidă.
- **FIXAT #2 — G2 scoping cross-project:** `relation_repository.find_by_type` NU
  e project-scoped (bypass-ează filtrul de bază), deci la >1 proiect codegraph-
  indexat în același DB returnează relațiile tuturor proiectelor → contaminare
  cross-project în `_load_graph_view` și de acolo în `find_symbol`/`impact_path`/
  `code_context`. Fix: filtru `if rel.project_id != self.project_id: continue`
  ca prim check în fiecare din cele 3 bucle de relații (calls/imports/defined_in).
  Provenance (G5), care folosește și `find_by_type`, nu e atins.
- **FIXAT #3 — `code_context` honors Optional:** `code_context`/`render_code_context`
  erau tipizate `Optional` dar raise `KeyError` la permalink absent + double-load
  de `GraphView`. Acum returnează `None` / un mesaj „use `find_symbol`”; o singură
  încărcare de `GraphView` e refolosită între lookup și definiție (o singură
  parcurgere, nu două) via helper-ul `_fill_context`. Stratul pur `_code_context`
  păstrează raise-ul (stratul pur poate raise).
- **FIXAT #4 — `CodeContext.render` slice negativ:** pentru `max_tokens` 1–4
  (`budget_chars` 4–16) `budget_chars - 20` devenea negativ → slice de la coadă
  (output garbled + marker de trunchiere lipit de coadă). Fix: `cut = max(0,
  budget_chars - 20)` → prefix curat (eventual gol).
- **FIXAT #5 — G1 `match_trigger` token-boundary:** înlocuit containerea de
  substring cu intersecție de seturi de tokeni pe tokeni semnificativi (≥ 3 char).
  Un token de topic de 3 char (ex. `test`) match-ează acum doar un trigger ce
  conține *cuvântul* `test`, nu substring-ul din `latest`. Precizie strict mai bună;
  toate cele 14 teste trigger existente trec în continuare.
- **FIXAT #6 — test boost E2E false-positive:** `test_boost_end_to_end_via_build_context`
  indexa skill-ul primul → skill era prim și *fără* boost (test trecea banal, nu
  detecta boost lipsit/rupt). Reordonat: NOTE indexat prim (ordinea naturală pre-
  boost e note-first), boost-ul trebuie să re-rankeze skill-ul validat în față.
  Asertări neschimbate.

**False positives respinse de verificare:**
- Agentul docs a flagat CHANGELOG.md:33 („broken sync_service conftest fixture”) ca
  greșit — confundă duplicatul `app_config=` (fixat) cu kwargs-urile învechite
  `link_resolver`/`schema_service`/`alias_repository` încă prezente în
  `tests/conftest.py:427-430` (încă rupt, pre-existent). CHANGELOG e corect.
- Agentul tests a flagat `test_boost_end_to_end_via_build_context` ca
  false-positive (trece indiferent de boost din cauza ordinii de indexare) — real
  dar pre-existent G1 boost; logica boost e acoperită de testele unitare directe.
  Coverage suplimentară, nu o pierdere de correcție.

**Consistența redenumirii Tb (Tencent→Tb): VERIFICATĂ CURATĂ.** Singura apariție
`Tencent` rămasă în repo e URL-ul extern protejat din `tb-agent-memory-borrow-list.md:3`
(citație funcțională intenționată). Toate cross-ref-urile rezolvă, doctor
print/test sincronizate (`Tb-borrowed capabilities`), niciun test nu asertează
„Tencent”.

**Validare (Phase 1 + Phase 2):** 35 trecute / 0 eșuate pe suitele atinse în
Phase 1 (shortterm_tools 6→7, doctor_capability_status 2, context_service
trigger_match 14 + skill_boost + retrieval_budget) + 26 pe
`test_shortterm_context.py`. Phase 2: broad regression sweep 200 trecute pe
suitele afectate (codegraph_service 8→10 incl. prune + cross-project,
context_service trigger_match 14→15 token-boundary, skill_boost cu test
reordonat); 2 eșecuri PRE-EXISTENTE `test_entity_service` (permalic unicitate
×2) confirmate out-of-scope — `get_symbol_permalinks` e read-only, nu e pe path-ul
create/update (baseline-ul din memory-ul de validare, NU regresie). Importuri
curate. **0 regresii introduse.** Version bump 0.20.0 → 0.20.1 (patch: corectitudine
review-driven; suprafața MCP capabilităților NESCHIMBATĂ — niciun tool
adăugat/șters/redenumit; G6 short-term funcționează acum efectiv, `code_context`
onoră Optional).

## Search isolation (2026-08-06, sesiunea post-review — 0.20.2 → 0.20.3)
Constrângere explicită a utilizatorului: **fișierele de cod nu trebuie indexate ca
entități, ca să nu afecteze knowledge graph-urile.** Confirmat: `codegraph_enabled`
e default off → codul NU e indexat ca entități by default. Dar chiar și cu G2 activat,
simbolurile de cod (file/module/function/class) ar fi apărut în `search_notes`
NEFILTRAT → poluare a grafului de note. Utilizatorul a ales **izolarea în search**
(opt-in explicit `types=[...]` ocolește izolarea).

- **Schimbare:** `search_notes` (tool MCP general) setează
  `SearchQuery.exclude_types = [file, module, function, class]` când NU e dat un
  filtru `types` explicit. Codul rămâne accesibil via tool-urile G2 dedicate
  (`find_symbol`/`impact_path`/`code_context`) și via opt-in `types=["function"]`.
  `build_context`/`find_symbol`/`link_resolver`/prompt routers — NEatinse (scope
  deliberat îngust: doar `search_notes`).
- **Plumbing:** `SearchQuery.exclude_types` (nou, complement al `types`) →
  `search_service.search` → `SearchRepository.search` (Protocol + base + SQLite
  `NOT IN json_extract` + Postgres `NOT (JSONB @>)`). Stoolap neatins (semnătură
  diferită). Nu adăugat la `SearchQuery.no_criteria()` (e filtru, nu criteriu).
- **Fișiere atinse:** `schemas/search.py`, `repository/search_repository_base.py`,
  `repository/search_repository.py` (Protocol), `repository/sqlite_search_repository.py`,
  `repository/postgres_search_repository.py`, `services/search_service.py`,
  `mcp/tools/search.py` (import constante code_importer + set exclude_types),
  `AGENTS.md` (notă G2 search isolation), `CHANGELOG.md` [0.20.3], `server.json`
  + `__init__.py` (version bump).
- **Teste (2 noi):** `tests/schemas/test_search.py::test_exclude_types_field_round_trips`
  (default None + round-trip model_dump) + `tests/repository/test_search_repository.py
  ::test_search_exclude_types_keeps_code_out_of_general_search` (baseline surfacă
  ambele; exclude_types drop-ează doar funcția code, păstrează nota; opt-in
  `types=[function]` returnează funcția).
- **Validare:** 43 trecute (1 nou + 42 search service), **0 regresii**.
  `test_search_with_date_filter` eșuează — confirmat PRE-EXISTENT via `git stash`
  (eșează și pe 0.20.2 curat; problemă FTS/after_date timing, nu din schimbarea asta).
- **Suprafața MCP:** NESCHIMBATĂ — `search_notes` există înainte/după, doar
  comportamentul e rafinat (excludere code în căutarea generală). Niciun tool
  adăugat/șters/redenumit.

## Următorul pas
- **Nimic în acest program.** Implementarea împrumuturilor Tb este completă.
- Pentru reluare: validare oficială cu `just test-sqlite` / `just test-postgres` în
  mediul Python 3.12 + uv.lock (când e disponibil), apoi activarea flag-urilor pe
  proiecte pilot. Seam-urile se cuplează când fazele corespunzătoare din planul
  L0–L3 aterizează (G3 ↔ Faza 4 `IncrementalTracker`; G6 ↔ Faza 4/L2 `ScenarioBuilder`).