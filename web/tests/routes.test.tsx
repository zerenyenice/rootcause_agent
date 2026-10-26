import { render, screen } from "@testing-library/react";
import CatalogRoute from "@/app/catalog/page";
import ConnectionsRoute from "@/app/connections/page";
import HomeRoute from "@/app/page";

vi.mock("@/components/chat/ChatPage", () => ({ ChatPage: () => <p>chat-page</p> }));
vi.mock("@/components/connections/ConnectionsPage", () => ({
  ConnectionsPage: () => <p>connections-page</p>,
}));
vi.mock("@/components/catalog/CatalogPage", () => ({
  CatalogPage: () => <p>catalog-page</p>,
}));

describe("app routes", () => {
  it("/ renders the chat page", () => {
    render(<HomeRoute />);
    expect(screen.getByText("chat-page")).toBeInTheDocument();
  });

  it("/connections renders the connections page", () => {
    render(<ConnectionsRoute />);
    expect(screen.getByText("connections-page")).toBeInTheDocument();
  });

  it("/catalog renders the catalog page", () => {
    render(<CatalogRoute />);
    expect(screen.getByText("catalog-page")).toBeInTheDocument();
  });
});
