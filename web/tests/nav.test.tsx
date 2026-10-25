import { render, screen, within } from "@testing-library/react";
import { Nav } from "@/components/Nav";

const navigation = vi.hoisted(() => ({ pathname: "/" }));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
}));

describe("Nav", () => {
  it("links to Chat, Connections and Catalog inside a navigation landmark", () => {
    navigation.pathname = "/";
    render(<Nav />);
    const nav = within(screen.getByRole("navigation"));

    expect(nav.getByRole("link", { name: "Chat" })).toHaveAttribute("href", "/");
    expect(nav.getByRole("link", { name: "Connections" })).toHaveAttribute(
      "href",
      "/connections",
    );
    expect(nav.getByRole("link", { name: "Catalog" })).toHaveAttribute(
      "href",
      "/catalog",
    );
  });

  it("marks only the current page's link with aria-current", () => {
    navigation.pathname = "/catalog";
    render(<Nav />);

    expect(screen.getByRole("link", { name: "Catalog" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Chat" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(screen.getByRole("link", { name: "Connections" })).not.toHaveAttribute(
      "aria-current",
    );
  });
});
