# RFCs (Request for Comments)

An RFC is a lightweight design document for a **major** change, written and
reviewed **before** implementation. It captures intent, design, alternatives,
and trade-offs so decisions are deliberate and durable rather than buried in
PR threads.

## When to write an RFC

Write an RFC when a change is any of:

- A new major feature or user-visible capability.
- An architectural change (new subsystem, cross-cutting refactor, change to a
  core data flow).
- A design decision that is hard to reverse or that other work will depend on.

Do **not** write an RFC for bug fixes, small refactors, dependency bumps, test
additions, or config tweaks — a PR is enough.

> Rule of thumb: if the change would need agreement from more than one person,
> or would shape future work, write an RFC.

## Lifecycle

| Status | Meaning |
|--------|---------|
| Draft | Being authored; not yet ready for review. |
| In Review | PR open; gathering comments. |
| Accepted | PR merged; this is the agreed design. Implementation may proceed. |
| Declined | Rejected; kept for the record. |
| Implemented | Code shipped; link the shipping PR(s) here. |

An Accepted RFC is a design contract, not a guarantee of implementation. Once
implemented, link the shipping PRs and set status to Implemented.

## How to submit

1. Copy `0000-template.md` to `NNNN-short-name.md` where `NNNN` is the next free
   number (zero-padded; start at `0001`). Use a kebab-case slug for the rest.
2. Fill in the sections.
3. Open a PR titled `rfc: <slug>`. Set status to `In Review`.
4. Address comments; when there is agreement, merge the PR and set status to
   `Accepted`.
5. Implement via normal issues/PRs; link them from the RFC; flip to
   `Implemented`.

Discussion happens on the RFC PR. Issues track concrete problems/bugs; an RFC
proposes the design. A key issue (e.g. a cross-cutting architectural decision)
may prompt an RFC.

## Index

| # | Title | Status |
|---|-------|--------|
| 0000 | RFC template (not a real RFC) | — |

(Add a row per accepted RFC. The template row stays for reference.)
