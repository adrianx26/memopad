# Plan de Implementare: Împrumuturi din TbDB-Agent-Memory în MemoPad

**Data:** 2026-08-06
**Relație cu planul existent:** Acest plan **completează** `memopad-levels-implementation-plan.md`,
NU îl înlocuiește. Se aplică pe feature-urile pe care planul L0–L3 **nu** le acoperă.
Toate referințele la cod sunt în `src/memopad/`.

> ## Status implementare (2026-08-06): ✅ G1–G7 FINALIZATE
>
> Toate cele 7 gap-uri sunt implementate, sub feature flags (default off), 100%
> backward-compatible. 0 regresii introduse (suita de bază are 7 eșecuri
> PRE-EXISTENTE nelegate de acest plan — permalink unicitate + CTE `near "?"`
> pe Python 3.14). Detalii complete + decizii de design + fișiere atinse +
> validare per item: **`tb-borrow-progress.md`**.
>
> | Gap | Status | Feature flag | Tool-uri MCP noi |
> |-----|--------|--------------|------------------|
> | G5 drill_down + provenance | ✅ | `levels_enabled` | `drill_down` |
> | G4 retrieval cap + timeout | ✅ | `recall_max_chars_per_memory` / `recall_timeout_ms` (0=off) | — (în `build_context`) |
> | G7 PersonaMem eval | ✅ | marker `@pytest.mark.eval` | — (`pytest -m eval`) |
> | G1 Skill asset | ✅ | `skills_enabled` | `create_skill` / `get_skill` / `list_skills` / `validate_skill` |
> | G3 trigger cadențe + warmup | ✅ | `levels_pipeline_automatic` | — (`DistillationScheduler`, seam pentru distiller) |
> | G6 short-term layering | ✅ | `shortterm_enabled` | `add_session_ref` / `add_session_step` / `get_session_context` / `drill_down_session` / `finalize_session` |
> | G2 CodeGraph | ✅ | `codegraph_enabled` | `index_code` / `find_symbol` / `impact_path` / `code_context` |
>
> **Seam-uri amânate intenționat** (documentate în progress doc, NU bug-uri):
> distiller-ul efectiv L0→L1→L2→L3 (G3 emite doar `DistillationTrigger`),
> summarizer LLM pentru steps G6, parser tree-sitter pentru G2, verificare LLM
> în `validate_skill` G1, cuplajul G3/G6 în hot path-ul create/sync.
>
> **Implementate în sesiunea de finalizare** (înainte amânate, acum finalizate):
> trigger-matching automat în `build_context` G1 (`match_trigger` +
> `_inject_trigger_skills`, 14 teste) și reindex watch G2 (`WatchService` full-tree
> reindex la batch cu fișiere sursă, factory `get_codegraph_service`, fallback
> manual `index_code`, 9 teste). Ambele rămân feature-flagged (default off).

## Ce NU împrumutăm (deja în planul L0–L3 sau deja există)

| Item | De ce nu |
|------|----------|
| Layering L0–L3 | Planul existent îl definește complet |
| Ranking formula + token_budget global | Plan existent §8 |
| Decay / pruning / reconfirmare | Plan existent §9 |
| Provenance `source_entities` | Plan existent §3.2 (frontmatter) |
| Hybrid search BM25+embedding+RRF | Deja implementat (`embedding_service.py`) |
| Stocare Markdown+SQLite eterogenă | Deja existent + plan §2 |

---

## Gap-urile pe care le acoperim din Tb (7)

### G1. Asset-ul "Skill" (cel mai valoros, lipsește complet)
Planul tău spune explicit "nu creăm un al 5-lea level" — corect. Dar Skill **nu e un level**,
e un **tip de asset ortogonal**: expertiză reutilizabilă, versionată, cu *trigger / steps / validation*.
MemoPad memorează "ce știe" (facts) dar nu "ce a învățat să facă bine" (skills).

**Modelare în MemoPad** (fără al 5-lea level):
- `entity_type = "skill"` (extinde `Entity.entity_type`)
- Reutilizează `ObservationSchema` ca registry de categorii canonicale pe skill:
  `[trigger]`, `[step]`, `[validation]`, `[when]`, `[do]`, `[don't]`
- Frontmatter:

```markdown
---
title: "Reset DB în siguranță"
type: skill
level: L2            # skill-urile trăiesc la L2 (scenarii reutilizabile)
permalink: skill-reset-db-safe
skill_version: 3
skill_status: validated    # draft | validated | deprecated
trigger: "când utilizatorul cere reset DB în prod"
source_entities:          # proveniență (deja în plan)
  - memory://entity/incident-reset-db-2026-07
tags: [db, ops]
---

- [trigger] "reset DB" + "prod" în cerere
- [step] 1. Snapshot -> 2. Notify -> 3. Reset -> 4. Verify checksum
- [validation] Checksum post-reset == pre-snapshot
- [don't] Nu rula fără snapshot pe prod
```

**Implementare:**
1. `src/memopad/services/skill_service.py` — CRUD + versionare (`skill_version`).
   Skill-ul nou = versiune nouă; versiunea veche rămâne cu `status: deprecated` (nu se șterge — traseabilitate).
2. `src/memopad/mcp/tools/skill.py` — `get_skill`, `list_skills`, `create_skill`, `validate_skill`.
3. `validate_skill` apelează LLM să verifice dacă pașii acoperă trigger-ul și validation rule se respectă.
4. Integrare în ranking (§8 plan existent): skill cu `status: validated` primește boost ca un L2 cu confidence 1.0.
5. Integrare în `build_context`: dacă query-ul se potrivește cu `[trigger]` unui skill, skill-ul se injectează prioritar.

**Efort:** 4–5 zile | **Depinde de:** Faza 1 a planului L0–L3 (entitate + frontmatter level)

---

### G2. CodeGraph — indexare de cod în graful existent (lipsește complet)
MemoPad indexează markdown, nu cod. Tb indexează simboluri, fișiere, relații de apel
și **impact paths** (ce afectează o schimbare). Cold-start valoros pentru proiecte noi.

**Modelare în MemoPad** (reutilizează Entity + Relation):
- `entity_type`: `file`, `function`, `class`, `module`
- Relații native (`relation_repository`): `calls`, `defined_in`, `imports`, `imports_from`,
  `impacts` (relație derivată: A impacts B dacă A e apelat de B)
- Permalink-uri: `code://proj/path/file.py::function_name`

**Implementare:**
1. `src/memopad/importers/code_importer.py` — parcurge repo cu tree-sitter (Python/TS/Go).
   Extrage simboluri (def/class/import) și relații. Reutilizează pattern-ul din `assimilate/crawler.py`.
2. Stocare: `code://` entities la **L0** (sursa de adevăr = fișierele), simboluri la **L1** (atom).
   Indexare BM25+embedding existentă, zero schimbare în `search_service`.
3. `src/memopad/mcp/tools/codegraph.py`:
   - `find_symbol` — caută simbol după nume, returnează definiție + callers.
   - `impact_path` — BFS pe relația `calls` inversă: "dacă schimb funcția X, ce afectează?".
   - `code_context` — pentru un simbol, returnează definiția + dependențele directe (token-budgeted).
4. Watch: extinde `sync/watch_service.py` ca la edit fișier `.py` să reindexeze doar delta simbolurilor.

**Efort:** 6–8 zile | **Depinde de:** nimic (independent de L0–L3; cod = L0/L1)
**Risc:** volume mari → constraintă la proiectul selectat + incremental.

---

### G3. Trigger-e eveniment cu cadențe validate (planul are doar manual)
Planul tău §7.3 listează trigger-e manuale (CLI/sync/cron). Tb adaugă cadențe numerice
validate + **warmup** + **idle timeout** — transformă distilarea din batch într-un proces reactiv.

**Parametri de adăugat în config (`MEMOPAD_LEVELS_*`):**
```yaml
pipeline:
  every_n_conversations: 5      # distilare L1 după 5 ingesteri noi
  max_memories_per_session: 20  # cap per pass (anti-poluare L1)
  l1_idle_timeout_seconds: 600  # dacă idle 10min, rulează L1 oricum
  l2_min_interval_seconds: 900  # nu re-trigger L2 sub 15min
  persona_trigger_every_n: 50   # regenerează L3 la 50 memorii noi
  enable_warmup: true           # sesiune nouă: extrage de la turn 1, dublând 1→2→4→N
```

**Implementare:**
1. `src/memopad/services/distillation_scheduler.py` — registru de evenimente pe proiect
   (new_entity, new_observation, idle), coada de trigger-uri cu debounce/min-interval.
2. Warmup: la primul entity al unei sesiuni noi → counter 1; la al 2lea → 2; la al 3lea → 4…
   (dublare) până la N. Logica în `IncrementalTracker` (plan §7.1).
3. Idle: watcher pe `watch_service` / heartbeat sesiune MCP; după `l1_idle_timeout` → trigger L1.
4. Toate trigger-ele rămân **opt-in** prin feature flag `levels_pipeline_automatic` (default off
   — păstrează comportamentul actual manual până se validează).

**Efort:** 3–4 zile | **Depinde de:** Faza 4 a planului (IncrementalTracker)

---

### G4. Retrieval: per-memory cap + timeout cu degradare grațioasă
Planul §8 are `token_budget` global + `max_L0_fraction`. Lipsesc două lucruri din Tb:
- `max_chars_per_memory` — cap per item (un singur L1 uriaș nu poate epuiza budget-ul)
- `timeout_ms` — la timeout, **skip injectare fără să blocheze** conversația

**Implementare:**
1. În `context_service.py` / `build_context`: adaugă `max_chars_per_memory` (taie/trunchează
   un item peste limită, cu marker `…[truncated]`).
2. Wrap retrieval (BM25 + embedding) în `asyncio.wait_for(..., timeout=timeout_ms)`;
   pe `TimeoutError` → log + returnează doar ce s-a obținut (degradare grațioasă), nu excepție.
3. Config: `recall.max_chars_per_memory`, `recall.timeout_ms` (default 5000).
4. Metrici: log `recall_truncated_count`, `recall_timeout_count` (pentru dashboard §10 plan).

**Efort:** 1–2 zile | **Depinde de:** Faza 2 a planului (retrieval)
**Risc:** trunchierea poate tăa informație — doar pe L0/L1 lungi, niciodată pe L3 (persona) care e mică.

---

### G5. Tool `drill_down` — proveniență reversibilă ca invariant expus
Planul stochează `source_entities` în frontmatter (§3.2), dar nu expune un instrument care
să **parcurgă lanțul** L3→L2→L1→L0. Principiul Tb: "top symbol → mid index → raw text",
determinist. Fără tool, link-urile există dar nu sunt utilizabile programatic.

**Implementare:**
1. `src/memopad/mcp/tools/drill_down.py` — input: permalink + nivel țintă (default L0).
   Urmează `source_entities` recursiv până la L0; returnează lanțul ca listă de permalink-uri
   + snippet-uri (token-budgeted).
2. Validare invariant: la crearea oricărui L1/L2/L3, **refuză** dacă `source_entities` e gol
   (fail-fast, conform AGENTS.md) — distilare fără proveniență = bug, nu warning.
3. Backref automat: când un L1 e folosit ca sursă de un L2, înregistrăm relația `derived_from`
   în `relation_repository` (relație nativă, căutabilă invers = backlinks deja existente).

**Efort:** 2 zile | **Depinde de:** Faza 1 (L1+L3 cu frontmatter source_entities)

---

### G6. Layering pe termen scurt în-task (separate de L0–L3)
Planul L0–L3 e memorie pe termen lung. Tb adaugă un strat **separat** de context pe sesiune:
`refs/*.md` (raw tool outputs) → JSONL (sumare pe pași) → **Mermaid canvas** (top, condensat).
Offload la 0.5 / 0.85 din fereastra de context, cap Mermaid la 0.2, drill-down via `node_id`.
MemoPad are `canvas.py` dar nu ca layering de context activ.

**Implementare:**
1. `src/memopad/services/shortterm_context.py` — 3 straturi per sesiune (în `.memopad/sessions/<id>/`):
   - `refs/*.md` — output-uri brute de tool-uri (ground truth în-task)
   - `steps.jsonl` — sumare pe pași (LLM, una pe tool-call)
   - `canvas.mmd` — stare condensată ca Mermaid (top layer, ce vede agentul)
2. Offload policy: când context atinge `mild_offload_ratio=0.5` → comprimă refs în steps;
   la `aggressive_compress_ratio=0.85` → regenerează canvas.mmd (cap `mmd_max_token_ratio=0.2`).
3. Extinde `mcp/tools/canvas.py` cu `drill_down node_id` → returnează ref-ul / step-ul sursă.
4. Nu atinge L0–L3: strat independent; la sfârșitul sesiunii, steps-urile stabili pot fi
   promovați la L2 (scenario) prin `ScenarioBuilder` — conexiunea cu planul existent.

**Efort:** 4–5 zile | **Depinde de:** nimic (independent; se cuplează cu Faza 4/L2 la final)
**Risc:** complexitate; scope strict pe sesiune, cleanup la sfârșit.

---

### G7. Benchmark PersonaMem + eval harness (măsurare)
Planul §14 are criterii calitative (tokeni ↓30%, ≥80% noduri). Tb raportează 48%→76%
pe PersonaMem. Fără un eval repetabil nu știi dacă L0–L3 chiar crește recall-ul.

**Implementare:**
1. `tests/eval/personamem_eval.py` — set mic de "preferințe utilizator" + întrebări după N
   conversații de diluție; măsoară recall-ul agentului cu vs fără L3 în context.
2. Rulează în CI (marcat `@pytest.mark.eval`, nu în `just test` default).
3. Adaugă la dashboard-ul de statistici (plan §10): scor PersonaMem + recall pe level.
4. Re-rulează după fiecare fază a planului L0–L3 pentru a valida că trece de la "pare mai bine" la "e mai bine".

**Efort:** 2–3 zile | **Depinde de:** Faza 1 (L3 funcțional)
**Valoare:** de绿灯 pentru restul investiției — fără el cheltuiești 28–43 zile pe intuiție.

---

## Matrice de încadrare în planul L0–L3 existent

| Împrumut | Se cuplează cu faza | Efort | Prioritate |
|----------|---------------------|-------|------------|
| G5 drill_down | Faza 1 (L1+L3) | 2 zile | P0 |
| G4 retrieval cap+timeout | Faza 2 (retrieval) | 1–2 zile | P0 |
| G1 Skill | Faza 1 (după entitate+level) | 4–5 zile | P1 |
| G3 trigger cadențe+warmup | Faza 4 (Incremental) | 3–4 zile | P1 |
| G7 PersonaMem eval | Faza 1 (după L3) | 2–3 zile | P1 |
| G6 short-term layering | Faza 4 (L2) | 4–5 zile | P2 |
| G2 CodeGraph | independent | 6–8 zile | P2 |

**Total adițional peste planul L0–L3:** ~22–29 zile (față de 28–43 zile planul de bază).

---

## Ordinea recomandată de atac

1. **G5 + G4** (P0, ieftine, finisează proveniența și retrieval-ul planului de bază)
2. **G7** (măsurare devreme — ca să validăm restul)
3. **G1 Skill** (capabilitate nouă de mare valoare, după ce L1/L2 sunt stabile)
4. **G3 trigger-e** (transformă distilarea în reactivă, după IncrementalTracker)
5. **G6 short-term** (strat independent de context pe sesiune)
6. **G2 CodeGraph** (cel mai mare efort, independent, la final sau în paralel)

---

## Principii păstrate (din AGENTS.md)

- **Fail-fast**: G5 refuză distilare fără proveniență (nu warning).
- **Fără fallback logic**: nu se adaugă fallback la retrieval (G4 face degradare explicită, nu ascunsă).
- **Minimize diffs**: G1/G2/G6 reutilizează Entity, Relation, ObservationSchema, canvas.py —
  fără tabele noi decât unde e strict necesar (skill_version ca coloană, nu tabel separat).
- **Full file read before edits**: la atingerea `context_service.py`, `search_service.py`,
  `build_context.py` — citire completă înainte de modificare.
- **100% backward-compatible**: toate împrumuturi sub feature flags
  (`levels_pipeline_automatic`, `skills_enabled`, `codegraph_enabled`, `shortterm_enabled`).

---

## Ce lipsește intenționat din Tb (NU împrumutăm)

- ACL multi-team + System/Team Admin — local-first single-user, overkill.
- Tokenizer BM25 jieba (`zh`) — MemoPad e EN/Ro; low value.
- MemoryPanel web UI (localhost:8125) — MemoPad e CLI/MCP-first; dashboard-ul planului §10 e suficient.
- npm packaging / MemoryProxy framework adapters — MemoPad e deja MCP, framework-agnostic.