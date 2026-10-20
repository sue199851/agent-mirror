# Agent Mirror · Three payoffs from your AI interaction data

> Every conversation you have with Claude Code / OpenClaw / any agent is a behavioral log
> more private than a diary. Which projects you *voluntarily* bring AI into, what hour you
> ask questions, how you follow up, how you vent frustration — that is **revealed-preference**
> data more honest than any questionnaire. Agent Mirror turns it into three things:
> a searchable knowledge base, a personality mirror, and a career compass.
> **Fully local. Your data never leaves your machine.**

**See it work first**: the end-to-end demo of a synthetic persona ("nightowl" — fabricated
data, real pipeline, reproducible): [🖼️ pipeline visualization](demo/report/portrait.html) ·
[personality report](demo/report/personality.html) ·
[career report](demo/report/career.html) · [LLM-generated quiz samples (different every
run)](demo/report/confirm-samples.md)

| Domain | Answers | Method | Command |
|--------|----------|--------|---------|
| 📚 **Knowledge base** | What have I been through? How did I solve X last time? | Full-text search + LLM QA | `am.py search / ask` |
| 🪞 **Personality mirror** | Who am I? What do my behavior patterns mean? | Big Five + BFAS 10 aspects | `am.py personality` |
| 🧭 **Career compass** | Where do I voluntarily invest energy? What environment gives me flow? | RIASEC + work values | `am.py career` |

Personality and career are deliberately **separate** domains — different scientific
traditions (trait psychology vs. vocational psychology), separately runnable, separately
shareable. Full methodology with literature anchors and a limitations statement:
**[METHODOLOGY.md](METHODOLOGY.md)** (Chinese).

## Three principles

1. **Local-first**: raw data never leaves your machine; LLM calls only receive
   anonymized statistical summaries (or redacted snippets, or can be turned off entirely)
2. **Scientific frameworks**: no MBTI as the backbone — psychometric gold standards
   (Big Five / BFAS / RIASEC), every score backed by behavioral evidence citations
3. **An iterable evidence library**: three-layer repo (static scientific frameworks →
   calibratable evidence maps → your personal snapshot timeline)

## Quick start

Requires Python ≥ 3.8, zero third-party dependencies (single-file CLI; PyYAML optional
for config/framework loading). Works out of the box with default paths for
Claude Code / OpenClaw / Hermes sessions.

```bash
python3 scripts/am.py scan               # ① data health check (three default sources)
python3 scripts/am.py build              # ② materialize messages.jsonl + FTS5 index
python3 scripts/am.py search "OOM"       # ③ search past sessions (instant, no LLM)
python3 scripts/am.py analyze            # ④ portrait draft: evidence panel + prototype
python3 scripts/am.py confirm            # ⑤ self-calibration: SJT scenarios + evidence
                                          #    confirmation + blind-spot self-rating
python3 scripts/am.py analyze            # ⑥ finalized: calibration status + signature
```

With an LLM key (optional, OpenAI-compatible endpoint in `config.yaml`):
`ask` summarizes answers with citations; `analyze --mode hybrid` adds an LLM
interpretation layer; `confirm` generates **fresh, persona-tailored scenario questions
every run** (falls back to a fixed question bank without a key).

- Reports land in `reports/<track>-<date>.md` + self-contained `.html` (open in a browser,
  print to PDF, share). Snapshots under `profiles/` are `diff`-able over time.
- Custom agent data sources plug in **with zero code** — a YAML field-mapping protocol:
  [CONFIG.md](CONFIG.md) (jsonl / json / event-stream shapes).

## Privacy stance

AI conversation logs are more private than diaries. All analysis is local by default;
any LLM call passes through a redaction pipeline first (configurable: statistics-only /
redacted snippets / LLM fully off — see `config.example.yaml`). This repository never
accepts user data uploads, and `profiles/` + `index/` + `reports/` are gitignored.

## Documentation

| Doc | Content |
|-----|---------|
| [README.md](README.md) | Full manual (Chinese) |
| [METHODOLOGY.md](METHODOLOGY.md) | Methods whitepaper: 4-layer pipeline, literature anchors, limitations |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Architecture: three domains, one data foundation |
| [DATAFLOW.md](DATAFLOW.md) | Interface contracts: two artifacts, LLM dependency matrix |
| [CONFIG.md](CONFIG.md) | Zero-code custom data source protocol |
| [demo/](demo/README.md) | Reproducible synthetic-persona demo |

## Roadmap

- [x] scan / build / search / ask / analyze / confirm / diff / export, custom sources
- [x] MIT License, reproducible demo, bilingual README
- [ ] `calibrate` — BFI-2 self-report comparison (score upgrade + validity loop)
- [ ] multi-turn `chat`, web-export connectors (ChatGPT / Claude export JSON)

## License

MIT — see [LICENSE](LICENSE). Contributing: see [CONTRIBUTING.md](CONTRIBUTING.md).
