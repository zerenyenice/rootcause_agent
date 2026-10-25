# web

Minimal Next.js 15 (App Router) chat UI for RootCause. It streams from a
LangGraph Server with `useStream` and renders the final WhyChain report as
markdown. MVP scope only: no auth, catalog or management screens.

## Run

```bash
cp .env.example .env.local   # optional; defaults match `langgraph dev`
npm ci
npm run dev                  # http://localhost:3000
```

| Variable                        | Default                 | Meaning                   |
| ------------------------------- | ----------------------- | ------------------------- |
| `NEXT_PUBLIC_LANGGRAPH_API_URL` | `http://localhost:2024` | LangGraph Server base URL |
| `NEXT_PUBLIC_ASSISTANT_ID`      | `rootcause`             | Registered graph name     |

## Scripts

`lint`, `typecheck`, `test` (Vitest + coverage, 80% threshold), `build`,
`format:check`.

## Security notes

- Assistant text is rendered with `react-markdown` + `remark-gfm` and no raw
  HTML (no `rehype-raw`); HTML in report text is shown as escaped text.
- Tool messages show the tool name only; payloads are never rendered.
- Human-in-the-loop approval (`run_sql`) is isolated in `src/lib/interrupt.ts`
  and resumes with `{decisions: [{type: "approve" | "reject"}]}`.
  The approval panel shows `description` and `args.sql` (read-only); no other args.
