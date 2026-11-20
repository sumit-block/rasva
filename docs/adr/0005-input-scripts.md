# ADR-0005: Narration text is English or Devanagari

- Status: Accepted (owner decision)
- Date: 2026-11-20
- PRD refs: language scope (Hindi, English, Hinglish)

## Context

The PRD lists Hindi, English and Hinglish. In the first listening check on the M2,
Roman Hinglish ("Krishna ne kaha") sounded poor with Kokoro, as expected: Kokoro
and the Hindi candidates (Chatterbox, IndicF5) only pronounce Hindi correctly from
Devanagari. Good Roman-to-Devanagari conversion would need a dedicated model
(e.g. AI4Bharat IndicXlit) and review effort.

## Decision

Scripts are written in **English (Latin) or Hindi (Devanagari)**. English words
inside a Devanagari sentence are fine. Roman Hinglish is out of scope for now.

- No transliteration model is added. The existing rule-based "Suggest Devanagari"
  helper stays as an optional aid; it is not developed further.
- Engine choice, benchmarks and listening tests use English and Devanagari text only.

## Consequences

- The PRD's Hinglish requirement is deferred, not dropped; revisit with IndicXlit
  if Roman input becomes necessary.
- The Confluence PRD should note this scope change.
