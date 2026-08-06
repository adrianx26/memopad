# Ce se merită împrumutat din TbDB-Agent-Memory în MemoPad

**Sursă analizată:** https://github.com/TencentCloud/TencentDB-Agent-Memory (Node.js, MIT)
**Data:** 2026-08-06
**Premisă:** MemoPad (Python) are deja: knowledge graph (Entities/Observations/Relations),
căutare hybridă BM25+embedding+RRF, link graph + backlinks, importers ChatGPT/Claude,
MCP server matur și plan L0–L3 propriu (draft 06.08). → Împrumut = concepte/patterns, nu cod.

---

## ⭐ Merită împrumutat (ranked)

### 1. Asset-ul "Skill" ca obiect first-class (cel mai valoros)
- **Ce are Tb:** expertiză reutilizabilă **versionată**, cu *trigger boundaries*
  (când se aplică), *execution steps*, *validation rules*.
- **Stare MemoPad:** entități/observații, dar nu există tipul "Skill".
- **Cum se aplică:** modelează Skill ca un `observation_schema` special ->
  "ce a învățat agentul să facă bine" devine recuperabil, nu doar "ce știe".

### 2. CodeGraph — indexare de cod în graful existent
- **Ce are Tb:** indexează simboluri, fișiere, relații de apel, **impact paths**
  (ce afectează o schimbare).
- **Stare MemoPad:** graf pentru markdown, nu indexează cod.
- **Cum se aplică:** fișiere/funcții ca entități, relații `calls`, `defined_in`,
  `imports`, `impacts` — reutilizează `link_resolver` + `backlinks` existente.
  Cold-start excelent pentru proiecte noi.

### 3. Lanț de proveniență reversibil (distilare traseabilă)
- **Ce are Tb:** orice compresie e reversibilă —
  *top-layer symbol -> mid-layer index -> bottom-layer raw text*.
- **Stare MemoPad:** sumarizare există, dar fără enforced provenance.
- **Cum se aplică:** fiecare L2/L3 păstrează `source_ids` -> backlink către L1/L0.
  Auditabilitate + reduce halucinațiile.

### 4. Retrieval budget-uri fine (pe lângă RRF)
- **Ce are Tb:** `maxCharsPerMemory`, `maxTotalRecallChars`, `timeoutMs`
  (pe timeout -> skip injectare fără să blocheze).
- **Stare MemoPad:** RRF da, dar fără per-memory char budget și fără timeout hard
  cu degradare grațioasă.
- **Cum se aplică:** peste `search_service` / `context_service`.

### 5. Trigger-uri concrete pentru pipeline-ul L0–L3
Default-uri validate, de preluat direct în planul L0–L3 din MemoPad:
- extragere la fiecare **5 conversații** (`pipeline.everyNConversations`)
- max **20 memorii/pass** (`extraction.maxMemoriesPerSession`)
- idle timeout **600s** pentru L1 (`pipeline.l1IdleTimeoutSeconds`)
- interval minim **900s** pentru L2 (`pipeline.l2MinIntervalSeconds`)
- persona regenerată la **50 memorii noi** (`persona.triggerEveryN`)
- **warmup**: sesiune nouă -> extrage de la turn 1, dublând 1->2->4...

### 6. Layering pe termen scurt în-task (separate de L0–L3)
- **Ce are Tb:** 3 straturi pe sesiune: `refs/*.md` (raw tool outputs) ->
  JSONL (sumare pe pași) -> **Mermaid canvas** (top, condensat).
  Offload la 0.5 / 0.85 din context window, cap Mermaid la 0.2, drill-down via `node_id`.
- **Stare MemoPad:** `canvas.py` există, dar nu ca layering de context.
- **Cum se aplică:** extinde `canvas.py` în acest pattern; rezolvă "tokeni prea mulți pe sesiune".

### 7. Stocare eterogenă ca regulă de design
- Principiu Tb: "lower layers preserve evidence (DB, full-text);
  upper layers preserve structure (Markdown, white-box)".
- **Cum se aplică:** L0/L1 în SQLite (full-text), L2/L3 ca Markdown human-readable.

### 8. Benchmark de calitate a memoriei (PersonaMem)
- **Ce are Tb:** 48% -> 76% (eval "agentul ține minte preferințele user-ului").
- **Stare MemoPad:** teste funcționale, fără benchmark de recall pe memorie.
- **Cum se aplică:** mic eval harness ca să măsoare dacă L0–L3 chiar crește recall-ul.

### 9. Visibility tags ușoare (varianta minimală din ACL)
- **Ce are Tb:** private/team/restricted + User/Role/Agent ACLs.
- **Stare MemoPad:** local-first single-user -> ACL multi-team nu se aplică.
- **Cum se aplică:** marchează memorii `private`/`restricted` și exclude-le
  din retrieval-ul implicit (memorii sensibile nu ajung în contextul agentului).

---

## ❌ NU se merită împrumutat (deja există sau nu se potrivește)

| Item | Motiv |
|------|-------|
| Hybrid search BM25+vector+RRF | Deja implementat (`embedding_service.py`, `search_service.py`) |
| Wiki link graph | Deja există (`link_resolver.py`, `backlinks.py`, `entity_parser.py`) |
| Portabilitate multi-agent / decuplare framework | MemoPad e deja MCP, framework-agnostic |
| ACL multi-team + System/Team Admin | Local-first single-user, overkill |
| Tokenizer BM25 jieba (`zh`/`en`) | MemoPad EN/Ro-centric, low priority |

---

## Priorizare sugerată pentru implementare

| Faza | Item | Efort | Impact |
|------|------|-------|--------|
| 1 | #5 Trigger-uri concrete L0–L3 | mic | mare (finisează planul existent) |
| 1 | #3 Proveniență reversibilă | mediu | mare (calitate) |
| 2 | #4 Budget-uri retrieval | mic | mediu |
| 2 | #1 Asset "Skill" | mediu | mare (capabilitate nouă) |
| 3 | #2 CodeGraph | mare | mare (cold-start cod) |
| 3 | #6 Layering în-task | mediu | mediu |
| 3 | #8 Benchmark PersonaMem | mediu | mediu (măsoară restul) |
| opt | #7/#9 | mic | mic |