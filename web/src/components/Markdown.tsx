import type { ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// SECURITY: report text comes from database values and model output.
// No rehype-raw: raw HTML is escaped as text, never parsed into elements.
// `singleTilde: false` keeps "~5 rows" from turning into strikethrough.
const REMARK_PLUGINS: ComponentProps<typeof ReactMarkdown>["remarkPlugins"] = [
  [remarkGfm, { singleTilde: false }],
];

const components: ComponentProps<typeof ReactMarkdown>["components"] = {
  // SECURITY: images would be fetched without a click (data exfiltration via
  // the URL), so they are replaced by their alt text.
  img: ({ alt }) => <span>{alt ?? ""}</span>,
  // A link whose sanitized href is empty (e.g. javascript:) is plain text.
  a: ({ children, href }) =>
    href ? (
      <a href={href} target="_blank" rel="noopener noreferrer nofollow">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  pre: ({ children }) => (
    <pre className="overflow-x-auto rounded-control border border-line bg-code p-3 text-[12.5px] leading-relaxed">
      {children}
    </pre>
  ),
  code: ({ children, className }) => (
    <code className={`font-mono ${className ?? ""}`.trim()}>{children}</code>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto rounded-control border border-line">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-line bg-subtle px-3 py-1.5 text-left font-medium">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-line px-3 py-1.5 last:border-b-0">{children}</td>
  ),
};

export function Markdown({ children }: { children: string }) {
  return (
    <div className="rc-prose">
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
