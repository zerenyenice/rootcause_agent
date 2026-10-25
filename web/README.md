# web

Minimal Next.js 15 (App Router) chat UI for RootCause. It streams from the
RootCause agent service (`rootcause.agent_service`) with `useStream` over the
SDK's `FetchStreamTransport` and renders the final WhyChain report as
markdown. MVP scope only: no auth, catalog or management screens.

## Run

```bash
cp .env.example .env.local   # optional; defaults match the local services
npm ci
npm run dev                  # http://localhost:3000
```

| Variable                | Default                 | Meaning                           |
| ----------------------- | ----------------------- | --------------------------------- |
| `NEXT_PUBLIC_AGENT_URL` | `http://localhost:8100` | Agent service base URL            |
| `NEXT_PUBLIC_API_URL`   | `http://localhost:8000` | Backend API (connections/catalog) |

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

## Why `.npmrc` sets `legacy-peer-deps=true`

With npm 10.9 and `vitest@4.1.12`, a plain `npm install` crashes with
`Cannot read properties of null (reading 'edgesOut')` while npm resolves
vitest's optional peers (`@vitest/browser-playwright`, `@vitest/browser-webdriverio`,
`@vitest/browser-preview`, `@vitest/ui`). Pinning those via `overrides` did not
avoid the crash, so the flag stays. Side effect: peers are not auto-installed, so
`@langchain/core` (peer of `@langchain/langgraph-sdk`) and `@testing-library/dom`
(peer of `@testing-library/react`) are listed explicitly in `package.json`.
Drop the flag (and re-check those two) once npm or vitest fixes this.
