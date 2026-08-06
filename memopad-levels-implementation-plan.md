# Plan de Implementare Complet: Level-uri de Memorie (L0–L3) + Procesare Vizuală în MemoPad

**Versiune:** 2.0  
**Data:** 2026-08-06  
**Status:** Draft detaliat pentru implementare

---

## Note de integrare cu împrumuturile Tb (2026-08-06)

Paralel cu acest plan s-au implementat 7 gap-uri împrumutate din TbDB-Agent-Memory
(`tb-borrow-implementation-plan.md`, toate ✅ sub feature flags). Două dintre ele
**se cuplează explicit** cu fazele de mai jos și trebuie reconnectate când faza respectivă
aterizează — punctele de cuplaj sunt deja documentate în cod și în `tb-borrow-progress.md`:

- **G3 — `DistillationScheduler` ↔ Faza 4 (`IncrementalTracker`).** G3 implementează
  *când* se distilează (cadențe `pipeline_every_n_conversations`, idle
  `pipeline_l1_idle_timeout_seconds`, debounce L2, persona `pipeline_persona_trigger_every_n`,
  warmup progresiv 1→2→4→…→128) ca motor de politică pur cu clock injectabil + stare
  in-memory per proiect, dar emite doar `DistillationTrigger` către un
  `DistillationCallback` no-op. **Cuplaj:** la crearea entității în `entity_service`,
  dacă `is_pipeline_active(cfg)` (`levels_enabled AND levels_pipeline_automatic`),
  se apelează `scheduler.record_new_memory(project_id)`; idle via heartbeat sesiune
  MCP / watcher. Când `FactDistiller`/`PersonaUpdater`/`ScenarioBuilder` (Faza 1/4)
  există, callback-ul devine apelul real — one-line hookup, fără schimbare în scheduler.

- **G6 — `ShortTermContext` ↔ Faza 4/L2 (`ScenarioBuilder`).** G6 e un strat
  **independent** de L0–L3, pe sesiune, file-backed (`<data_dir>/sessions/<id>/`:
  `refs/*.md` → `steps.jsonl` → `canvas.mmd`), cu offload la 0.5/0.85 din token budget
  și cap Mermaid la 0.2. `finalize_session` returnează `stable_steps()` (pașii
  persistați) — **inputul determinist** pentru un `ScenarioBuilder` viitor care va
  minta o entitate L2 scenario din pașii stabili ai sesiunii. Nu depinde de
  distiller; se cuplează la finalul Fazei 4.

Restul împrumuturilor (G1 Skill, G2 CodeGraph, G4 retrieval cap+timeout, G5 `drill_down`,
G7 PersonaMem eval) sunt fie independente de L0–L3, fie se integrează deja în flow-urile
existente (`build_context` pentru G1 skill boost + G4 trunc/timeout + G5 provenance);
detalii în `tb-borrow-implementation-plan.md` și `tb-borrow-progress.md`.

---

## Cuprins

1. [Context & Obiective](#1-context--obiective)
2. [Arhitectura generală](#2-arhitectura-generală)
3. [Modelul de date (Schema)](#3-modelul-de-date-schema)
4. [Procesarea conținutului text](#4-procesarea-conținutului-text)
5. [Procesarea conținutului vizual (Imagini, Diagrame, Schițe)](#5-procesarea-conținutului-vizual)
6. [Extragerea nodurilor și relațiilor din diagrame](#6-extragerea-nodurilor-și-relațiilor-din-diagrame)
7. [Pipeline de distilare](#7-pipeline-de-distilare)
8. [Retrieval, Ranking & Token Budget](#8-retrieval-ranking--token-budget)
9. [Decay, Pruning & Mentenanță](#9-decay-pruning--mentenanță)
10. [MCP Tools & CLI](#10-mcp-tools--cli)
11. [Faze de implementare](#11-faze-de-implementare)
12. [Estimări de cost & efort](#12-estimări-de-cost--efort)
13. [Riscuri & Mitigări](#13-riscuri--mitigări)
14. [Criterii de succes](#14-criterii-de-succes)
15. [Anexe](#15-anexe)

---

## 1. Context & Obiective

### 1.1 Situația actuală

MemoPad are deja:
- Knowledge graph (Entities + Observations + Relations)
- Căutare hybrid (BM25 + embedding)
- Stocare local-first în Markdown + SQLite
- MCP server matur

**Date actuale:**
| Metrică | Valoare |
|---------|---------|
| Entities | 12.200 |
| Observations | 66.478 |
| Relations | 14.083 |
| Projects | 10 |

### 1.2 Probleme pe care le rezolvă Level-urile

| Problemă | Cum o rezolvă L0–L3 |
|----------|---------------------|
| Tokeni prea mulți pe sesiune | Context selectiv (L3 + L1 relevante) |
| Zgomot la volume mari | Doar informația distilată urcă |
| Pierdere de coerență pe termen lung | L3 (Persona) stabil |
| Diagrame/imagini greu de folosit | Extragere structură + descriere + link înapoi |
| Scalare slabă | Ranking + decay + abstractizare |

### 1.3 Obiective cuantificate

| Obiectiv | Țintă realistă |
|----------|----------------|
| Reducere tokeni pe sesiune | −35% … −45% |
| Precizie pe fapte / preferințe | +35% … +55% |
| Succes pe task-uri coding lungi | +7% … +12% relative |
| Succes pe research / knowledge | +25% … +40% relative |
| Compatibilitate | 100% backward-compatible cu modelul actual |

---

## 2. Arhitectura generală

```
┌─────────────────────────────────────────────────────────────────────┐
│                        INGESTIE (orice sursă)                       │
│  Carte / PDF / Markdown / Imagine / Diagramă / Schiță / Conversație │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  L0 – RAW (Sursa de adevăr)                                         │
│  • Fișiere Markdown (text)                                          │
│  • Assets (imagini, diagrame, schițe)                               │
│  • Entities + Observations + Relations existente                    │
│  • Niciodată șters / modificat automat                              │
└────────────────────────────┬────────────────────────────────────────┘
                             │ Distilare (incrementală)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  L1 – ATOMIC FACTS                                                  │
│  • Fapte, definiții, preferințe, constrângeri, reguli               │
│  • Descrieri de imagini / diagrame                                  │
│  • Noduri extrase din diagrame                                      │
└────────────────────────────┬────────────────────────────────────────┘
                             │ Agregare
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  L2 – SCENARIOS                                                     │
│  • Tipare de lucru, procese, metode                                 │
│  • Fluxuri extrase din diagrame (secvențe de noduri)                │
│  • Context de proiect / domeniu                                     │
└────────────────────────────┬────────────────────────────────────────┘
                             │ Sinteză pe termen lung
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  L3 – CORE / PERSONA                                                │
│  • Profil stabil (stil, preferințe, reguli de aur)                  │
│  • Influențe din cărți / experiență                                 │
│  • Knowledge de domeniu foarte stabil                               │
└─────────────────────────────────────────────────────────────────────┘
```

**Principii de design:**
1. L0 este **singura sursă de adevăr** (editabilă de om).
2. L1–L3 sunt **derivate** și pot fi regenerate.
3. Totul rămâne local (Markdown + SQLite).
4. Distilarea este **incrementală**.
5. Orice informație din L1–L3 are link înapoi spre L0 (trazabilitate).
6. Knowledge graph-ul existent (Relations) este extins, nu înlocuit.

---

## 3. Modelul de date (Schema)

### 3.1 Extinderi în baza de date

```sql
-- Extinderi pe tabela entities / observations
ALTER TABLE entities ADD COLUMN level TEXT DEFAULT 'L0';  -- L0 | L1 | L2 | L3
ALTER TABLE entities ADD COLUMN confidence REAL DEFAULT 1.0;
ALTER TABLE entities ADD COLUMN source_entities JSON;       -- listă de memory:// 
ALTER TABLE entities ADD COLUMN source_assets JSON;         -- căi spre imagini
ALTER TABLE entities ADD COLUMN last_confirmed DATE;
ALTER TABLE entities ADD COLUMN importance_score REAL DEFAULT 0.5;
ALTER TABLE entities ADD COLUMN decay_factor REAL DEFAULT 1.0;
```

### 3.2 Frontmatter standardizat

#### L1 – Atomic Fact

```markdown
---
title: "Legea lui Ohm"
type: fact
level: L1
permalink: fact-ohms-law
source_entities:
  - memory://entity/carte-electrotehnica-cap3
source_assets:
  - assets/ohms-law-diagram.png
confidence: 0.95
last_confirmed: 2026-08-06
importance_score: 0.88
tags: [fizică, electricitate]
project: electrotehnica
---

- [definition] V = I × R
- [unit] Tensiunea se măsoară în Volți
```

#### L1 – Nod din diagramă

```markdown
---
title: "Encoder (Transformer)"
type: node
level: L1
permalink: node-transformer-encoder
source_assets:
  - assets/transformer-architecture.png
diagram_id: diag-transformer-001
confidence: 0.91
---

- [component] Encoder stack cu N straturi identice
- [contains] Multi-Head Attention + Feed Forward
```

#### L2 – Scenario / Flux

```markdown
---
title: "Fluxul de antrenare Transformer"
type: scenario
level: L2
permalink: scenario-transformer-training
related_nodes:
  - memory://node/transformer-encoder
  - memory://node/transformer-decoder
related_facts:
  - memory://fact/self-attention
source_assets:
  - assets/transformer-architecture.png
---

## Pași
1. Input Embedding + Positional Encoding
2. Encoder stack
3. Decoder stack (cu masked attention)
4. Linear + Softmax
```

#### L3 – Persona / Core

```markdown
---
title: "Persona principală"
type: persona
level: L3
permalink: persona-main
last_updated: 2026-08-06
---

## Stil de coding
- Preferă type hints stricte
- Teste înainte de implementare pe logică complexă

## Influențe stabile
- Abordarea incrementală din cartea X
- Principiul "simplitate înainte de optimizare"
```

### 3.3 Structură pe disk

```
~/.memopad/
├── projects/
│   └── <project>/
│       ├── notes/                  # L0 (existent)
│       └── assets/                 # imagini, diagrame, schițe
├── levels/
│   ├── L1/
│   │   ├── facts/
│   │   └── nodes/                  # noduri extrase din diagrame
│   ├── L2/
│   │   └── scenarios/
│   └── L3/
│       ├── persona.md
│       └── domain/                 # knowledge de domeniu foarte stabil
└── .memopad/
    └── distillation_state.json     # tracking incremental
```

---

## 4. Procesarea conținutului text

### 4.1 Flux pentru o carte / document lung

```
PDF / EPUB / Markdown
        │
        ▼
1. Conversie → Markdown curat (capitole / secțiuni)
2. Creare Entities L0 (câte un entity pe capitol sau pe secțiune mare)
3. Extragere Observations din text
4. Indexare BM25 + embedding (existent)
        │
        ▼
5. Distilare L1 (fapte, definiții, afirmații)
6. Agregare L2 (metode, procese, capitole-cheie)
7. Actualizare L3 (doar ideile foarte stabile / influențele)
```

### 4.2 Reguli de distilare text → L1

Un Observation devine candidat L1 dacă:
- Este o afirmație factuală / definiție / regulă / preferință
- Are confidence estimată > 0.7
- Nu este redundant cu un L1 existent (similaritate semantică)

---

## 5. Procesarea conținutului vizual

### 5.1 Tipuri de conținut vizual suportate

| Tip | Exemple | Strategie |
|-----|---------|---------|
| **Imagine foto / ilustrație** | Poze, screenshots | Salvare + descriere |
| **Diagramă tehnică** | Arhitectură, flowchart, UML, ERD | Salvare + extragere noduri + relații |
| **Schiță / drawing** | Schițe de mână, whiteboard | Salvare + descriere + OCR (dacă are text) |
| **Tabel complex** | Tabele din cărți | Extragere date + salvare imagine |
| **Formule / ecuații** | Ca imagine | OCR matematic + salvare |

### 5.2 Pipeline comun pentru orice asset vizual

```
Fișier imagine (png/jpg/svg/webp)
        │
        ▼
1. Salvare în assets/ (L0) + generare hash
2. Generare descriere (LLM Vision) → text
3. Detectare tip (diagramă vs. foto vs. schiță)
4. Dacă e diagramă → trece la pipeline-ul de noduri/relații (secțiunea 6)
5. Creare L1 de tip "description" + link spre asset
6. (Opțional) OCR dacă există text în imagine
```

### 5.3 Frontmatter pentru asset-uri

```markdown
---
title: "Arhitectura Transformer – figura 3.1"
type: asset
level: L0
permalink: asset-transformer-fig-31
file_path: assets/transformer-architecture.png
asset_type: diagram          # diagram | photo | sketch | table | formula
width: 1200
height: 800
hash: sha256:abc123...
extracted_nodes: true
---
```

---

## 6. Extragerea nodurilor și relațiilor din diagrame

Aceasta este una dintre cele mai valoroase părți ale planului.

### 6.1 Obiectiv

Dintr-o diagramă (flowchart, arhitectură, UML, mindmap etc.) să extragem:
- **Noduri** (componente, stări, entități)
- **Relații** (săgeți, conexiuni, dependențe)
- **Atribute** (etichete, direcție, tip de relație)

Și să le transformăm în Entities + Relations native MemoPad.

### 6.2 Pipeline detaliat

```
Imagine diagramă
        │
        ▼
┌──────────────────────────────────────────┐
│ 1. Preprocesare                          │
│    - Normalizare rezoluție               │
│    - Eventual vectorizare (dacă e scan)  │
└──────────────────┬───────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────┐
│ 2. LLM Vision – Structură               │
│    Prompt specializat:                   │
│    "Identifică toate nodurile și         │
│     relațiile. Returnează JSON."         │
└──────────────────┬───────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────┐
│ 3. Parsing & Validare JSON               │
│    - Noduri: id, label, type, position   │
│    - Relații: from, to, label, direction │
└──────────────────┬───────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────┐
│ 4. Creare Entities L1 (type: node)       │
│ 5. Creare Relations native MemoPad       │
│ 6. Link înapoi spre asset-ul original    │
└──────────────────────────────────────────┘
```

### 6.3 Schema JSON intermediară (output de la Vision)

```json
{
  "diagram_id": "diag-transformer-001",
  "title": "Transformer Architecture",
  "nodes": [
    {
      "id": "n1",
      "label": "Input Embedding",
      "type": "component",
      "description": "Convertește tokenii în vectori"
    },
    {
      "id": "n2",
      "label": "Multi-Head Attention",
      "type": "component"
    }
  ],
  "relations": [
    {
      "from": "n1",
      "to": "n2",
      "label": "feeds into",
      "direction": "forward"
    }
  ]
}
```

### 6.4 Transformare în modelul MemoPad

**Nod → Entity L1**
```markdown
---
title: "Multi-Head Attention"
type: node
level: L1
permalink: node-transformer-mha
diagram_id: diag-transformer-001
source_assets: [assets/transformer-architecture.png]
---
```

**Relație → Relation nativă**
```markdown
- feeds_into [[Multi-Head Attention]]
- part_of [[Transformer Encoder]]
```

### 6.5 Tipuri de diagrame prioritare (ordinea de suport)

| Prioritate | Tip diagramă | Complexitate extragere |
|------------|--------------|------------------------|
| P0 | Flowchart / Process | Medie |
| P0 | Arhitectură software (cutii + săgeți) | Medie |
| P1 | UML (class, sequence) | Ridicată |
| P1 | ERD / Data model | Medie-Ridicată |
| P2 | Mindmap | Medie |
| P2 | Schițe de mână (whiteboard) | Ridicată (nevoie de toleranță la zgomot) |

### 6.6 Prompt de bază pentru extragere (schelet)

```
Analizează diagrama. 
Returnează strict JSON cu această structură:
{
  "nodes": [{"id": "...", "label": "...", "type": "...", "description": "..."}],
  "relations": [{"from": "...", "to": "...", "label": "...", "direction": "forward|bidirectional"}]
}

Reguli:
- Nu inventa noduri care nu există vizual.
- Păstrează etichetele originale pe cât posibil.
- Dacă o săgeată nu are etichetă, folosește "connects_to".
```

---

## 7. Pipeline de distilare

### 7.1 Componente

| Componentă | Responsabilitate | Prioritate |
|------------|------------------|------------|
| `IngestionService` | Primește carte/imagine/document și creează L0 | P0 |
| `VisionExtractor` | Descriere + detectare tip asset | P0 |
| `DiagramParser` | Extrage noduri + relații | P0 |
| `FactDistiller` | Text/Observations → L1 Facts | P0 |
| `ScenarioBuilder` | L1 → L2 | P1 |
| `PersonaUpdater` | L1 stabile → L3 | P0 |
| `IncrementalTracker` | Știe ce s-a schimbat de la ultima rulare | P0 |
| `DecayEngine` | Scade confidence + pruning | P1 |

### 7.2 Flux incremental

```
La fiecare `memopad distill` sau sync --distill:

1. IncrementalTracker compară hash-uri / mtime / db version
2. Selectează doar entitățile / asset-urile noi sau modificate
3. Rulează pipeline-ul doar pe delta
4. Actualizează distillation_state.json
```

### 7.3 Trigger-e

- CLI: `memopad distill [--project X] [--full] [--dry-run] [--levels L1,L3]`
- MCP tool: `distill_memory`
- Opțional la `memopad sync --distill`
- Job background (cron / systemd timer)

---

## 8. Retrieval, Ranking & Token Budget

### 8.1 Formula de ranking

```
final_score = 
    α * semantic_score +
    β * bm25_score +
    γ * level_weight +
    δ * recency_score +
    ε * confidence +
    ζ * importance_score
```

**Greutăți recomandate inițiale:**
| Factor | Greutate |
|--------|----------|
| semantic | 0.30 |
| bm25 | 0.20 |
| level_weight | 0.25 |
| recency | 0.10 |
| confidence | 0.10 |
| importance | 0.05 |

**Level weights:**
- L3 = 1.00
- L2 = 0.85
- L1 = 0.70
- L0 = 0.40

### 8.2 Token Budget

```yaml
# config
memory:
  token_budget: 10000          # maxim tokeni de memorie injectați
  prefer_levels: ["L3", "L1", "L2"]
  fallback_to_L0: true
  max_L0_fraction: 0.3         # maxim 30% din budget din L0
```

Algoritm:
1. Încarcă întotdeauna L3 (dacă există).
2. Adaugă L1 + L2 sortate după `final_score` până se atinge budget-ul.
3. Dacă mai rămâne loc și `fallback_to_L0=true`, completează cu L0.

---

## 9. Decay, Pruning & Mentenanță

| Mecanism | Descriere | Parametru implicit |
|----------|-----------|--------------------|
| **Confidence decay** | Scade confidence dacă nu e reconfirmat | −0.05 / 90 zile |
| **Pruning** | Arhivează L1 cu confidence < 0.35 | după 180 zile |
| **Importance boost** | Crește importance când e folosit în context | +0.02 per hit |
| **Reconfirmare** | Când un L1 e regăsit în L0 nou → confidence = max(current, 0.9) | — |

---

## 10. MCP Tools & CLI

### 10.1 Tool-uri MCP noi / extinse

| Tool | Descriere |
|------|---------|
| `distill_memory` | Rulează distilarea (full/incremental/pe proiect) |
| `get_persona` | Returnează L3 |
| `list_facts` | Listează L1 (filtre: tag, project, min_confidence) |
| `list_scenarios` | Listează L2 |
| `list_nodes` | Listează noduri extrase din diagrame |
| `get_diagram` | Returnează structura (noduri+relații) a unei diagrame |
| `build_context` | Extins cu `prefer_levels` + `token_budget` |
| `ingest_asset` | Adaugă o imagine/diagramă și o procesează |

### 10.2 CLI

```bash
memopad distill [--project NAME] [--full] [--dry-run] [--levels L1,L2,L3]
memopad levels stats
memopad levels prune --min-confidence 0.35
memopad ingest asset assets/diagrama.png --type diagram
```

---

## 11. Faze de implementare

### Faza 0 – Fundație (2–3 zile)
- [ ] Migrare DB (câmpuri noi)
- [ ] Configurare (`MEMOPAD_LEVELS_*`)
- [ ] Structură de foldere `levels/`
- [ ] Feature flag `levels_enabled`

### Faza 1 – MVP Text (L3 + L1) (6–9 zile)
- [ ] FactDistiller
- [ ] PersonaUpdater
- [ ] Stocare Markdown L1/L3 + indexare
- [ ] Tool-uri `get_persona`, `list_facts`
- [ ] Integrare minimă în `build_context`
- [ ] Test pe 1–2 proiecte reale

**Rezultat așteptat:** −25–35% tokeni + coerență vizibil mai bună.

### Faza 2 – Retrieval inteligent (4–6 zile)
- [ ] Ranking complet (formulă de mai sus)
- [ ] Token budget enforcement
- [ ] Logging de metrici (tokeni pe level)
- [ ] Teste A/B pe relevanță

### Faza 3 – Vizual + Diagrame (7–11 zile)
- [ ] VisionExtractor (descriere + tip)
- [ ] DiagramParser (noduri + relații)
- [ ] Creare Entities `type: node` + Relations
- [ ] Tool-uri `list_nodes`, `get_diagram`, `ingest_asset`
- [ ] Test pe diagrame reale (arhitectură, flowchart)

### Faza 4 – L2 + Incremental + Decay (5–8 zile)
- [ ] ScenarioBuilder
- [ ] IncrementalTracker robust
- [ ] DecayEngine + pruning
- [ ] CLI complet

### Faza 5 – Productizare (4–6 zile)
- [ ] Documentație
- [ ] Teste de regresie pe volumul real (12k entities)
- [ ] Dashboard simplu de statistici
- [ ] Optimizări de performanță

**Total estimat:** 28–43 zile de lucru (1 persoană).

---

## 12. Estimări de cost & efort

### 12.1 Costuri LLM (volum actual)

| Operație | Tokeni estimați | Frecvență |
|----------|-----------------|-----------|
| Distilare L1 full (text) | 80k – 180k | O dată |
| Vision + diagrame (estimare 200–400 imagini) | 40k – 120k | O dată |
| Actualizare L3 | 8k – 25k | Lunar |
| Incremental săptămânal | 3k – 15k | Săptămânal |
| L2 | 15k – 40k | După L1 stabil |

### 12.2 Efort de dezvoltare

| Fază | Zile | Prioritate |
|------|------|----------|
| 0 – Fundație | 2–3 | P0 |
| 1 – MVP Text | 6–9 | P0 |
| 2 – Retrieval | 4–6 | P0 |
| 3 – Vizual + Diagrame | 7–11 | P0 |
| 4 – L2 + Decay | 5–8 | P1 |
| 5 – Productizare | 4–6 | P1 |
| **Total** | **28–43** | |

---

## 13. Riscuri & Mitigări

| Risc | Probabilitate | Impact | Mitigare |
|------|---------------|--------|----------|
| Calitate slabă la extragerea din diagrame | Mare | Mare | Human review pe sample + confidence threshold + fallback la descriere simplă |
| Costuri LLM ridicate la bulk | Medie | Medie | Model ieftin pentru bulk + incremental |
| Poluare L1 | Medie | Mare | Decay + ranking + deduplicare semantică |
| Regresii pe search existent | Medie | Mare | Feature flag + teste A/B |
| Complexitate crescută | Mare | Medie | Documentație clară + păstrare L0 ca fallback absolut |
| Diagrame foarte complexe / schițe | Mare | Medie | Suport treptat (întâi flowchart & arhitectură curată) |

---

## 14. Criterii de succes

### După Faza 1–2
- [ ] Tokeni medii pe sesiune ↓ minim 30%
- [ ] `get_persona` returnează informații corecte și stabile
- [ ] `build_context` respectă token budget și preferă L3+L1
- [ ] Zero regresii pe tool-urile MCP existente

### După Faza 3
- [ ] Din diagrame simple se extrag corect ≥ 80% din noduri și relații (evaluare manuală pe sample)
- [ ] Nodurile apar ca Entities căutabile
- [ ] Relațiile apar în knowledge graph

### După Faza 4–5
- [ ] Distilare incrementală rulează stabil pe volumul real
- [ ] Decay + pruning funcționează fără a pierde informație valoroasă
- [ ] Documentație completă + exemple

---

## 15. Anexe

### A. Ordinea recomandată de atac (rezumat)

1. Fundație + L3 + L1 (text)  
2. Ranking + Token Budget  
3. Pipeline vizual + extragere noduri/relații din diagrame  
4. L2 Scenarios  
5. Decay + Pruning + Productizare  

### B. Decizie de design importantă

> **Nu creăm un al 5-lea level.**  
> 4 level-uri (L0–L3) sunt suficiente. Nodurile din diagrame sunt L1 (`type: node`), iar fluxurile sunt L2 (`type: scenario`).

### C. Compatibilitate

- Toate notele existente rămân L0.
- Knowledge graph-ul existent continuă să funcționeze.
- Level-urile sunt un strat **adăugat**, nu o rescriere.

---

**Următorul pas recomandat:**  
Începerea Fazei 0 + Fazei 1 (MVP text) și măsurarea impactului real pe 1–2 proiecte înainte de a investi în pipeline-ul de diagrame.
