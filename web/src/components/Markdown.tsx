import type { ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// SECURITY: report text comes from database values and model output.
// No rehype-raw: raw HTML is escaped as text, never parsed into elements.
const REMARK_PLUGINS = [remarkGfm];

const components: ComponentProps<typeof ReactMarkdown>["components"] = {
  // SECURITY: images would be fetched without a click (data exfiltration via
  // the URL), so they are replaced by their alt text.
  img: ({ alt }) => <span>{alt ?? ""}</span>,
  // A link whose sanitized href is empty (e.g. javascript:) is plain text.
  a: ({ children, href }) =>
    href ? (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer nofollow"
        className="underline"
      >
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded bg-gray-100 p-3 font-mono text-sm">
      {children}
    </pre>
  ),
  code: ({ children, className }) => (
    <code className={`font-mono text-sm ${className ?? ""}`.trim()}>{children}</code>
  ),
  table: ({ children }) => (
    <table className="my-2 border-collapse border border-gray-300 text-sm">
      {children}
    </table>
  ),
  th: ({ children }) => <th className="border border-gray-300 px-2 py-1">{children}</th>,
  td: ({ children }) => <td className="border border-gray-300 px-2 py-1">{children}</td>,
};

export function Markdown({ children }: { children: string }) {
  return (
    <div className="space-y-2 break-words">
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
