import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { CopyButton } from "@/components/ui/CopyButton";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";
import { Menu } from "@/components/ui/Menu";
import { Segmented } from "@/components/ui/Segmented";
import { Skeleton } from "@/components/ui/Skeleton";
import { ToastProvider, useToast } from "@/components/ui/Toast";
import { Tooltip } from "@/components/ui/Tooltip";
import { cn } from "@/lib/cn";

describe("cn", () => {
  it("joins truthy class names", () => {
    expect(cn("a", false, null, undefined, "b")).toBe("a b");
  });
});

describe("Button", () => {
  it("is disabled and shows a spinner while loading", () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toBeDisabled();
    expect(button.querySelector("svg")).not.toBeNull();
  });

  it("applies variants and defaults to type=button", () => {
    render(<Button variant="danger">Delete</Button>);
    expect(screen.getByRole("button")).toHaveAttribute("type", "button");
  });
});

describe("simple primitives", () => {
  it("renders badge, card, skeleton, empty state and tooltip", () => {
    render(
      <>
        <Badge tone="ok">Verified</Badge>
        <Card data-testid="card">x</Card>
        <Skeleton />
        <EmptyState
          title="Nothing here"
          description="Add one"
          action={<button>Add</button>}
        />
        <Tooltip label="Hint">
          <button>Target</button>
        </Tooltip>
      </>,
    );
    expect(screen.getByText("Verified")).toBeVisible();
    expect(screen.getByTestId("card")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Nothing here" })).toBeVisible();
    expect(screen.getByRole("tooltip", { hidden: true })).toHaveTextContent("Hint");
  });

  it("renders banners with titles and the right live role", () => {
    render(
      <>
        <Banner title="Oops">Details</Banner>
        <Banner tone="info" alert={false}>
          FYI
        </Banner>
      </>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Oops");
    expect(screen.getByRole("status")).toHaveTextContent("FYI");
  });
});

describe("Field", () => {
  it("wires label, hint and error to the control", () => {
    render(
      <>
        <Field id="a" label="Name" error="Required">
          {(props) => <Input {...props} />}
        </Field>
        <Field id="b" label="Notes" hint="Optional">
          {(props) => <Textarea {...props} />}
        </Field>
        <Field id="c" label="Mode">
          {(props) => (
            <Select {...props}>
              <option>x</option>
            </Select>
          )}
        </Field>
      </>,
    );
    const name = screen.getByLabelText("Name");
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAccessibleDescription("Required");
    expect(screen.getByLabelText("Notes")).toHaveAccessibleDescription("Optional");
    expect(screen.getByLabelText("Mode")).toHaveAttribute("aria-invalid", "false");
  });
});

describe("Segmented", () => {
  it("is a radio group that reports changes", async () => {
    const onChange = vi.fn();
    render(
      <Segmented
        name="kind"
        label="Kind"
        value="a"
        onChange={onChange}
        fullWidth
        options={[
          { value: "a", label: "A" },
          { value: "b", label: "B" },
        ]}
      />,
    );
    expect(screen.getByRole("radiogroup", { name: "Kind" })).toBeVisible();
    expect(screen.getByLabelText("A")).toBeChecked();
    await userEvent.click(screen.getByLabelText("B"));
    expect(onChange).toHaveBeenCalledWith("b");
  });
});

describe("Dialog", () => {
  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button onClick={() => setOpen(true)}>Open</button>
        <Dialog
          open={open}
          title="Hello"
          description="Desc"
          onClose={() => setOpen(false)}
        >
          <input aria-label="First" />
          <input aria-label="Second" />
        </Dialog>
      </>
    );
  }

  it("focuses inside, traps Tab, closes on Escape and returns focus", async () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open" });
    await userEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Hello" });
    expect(dialog).toHaveAccessibleDescription("Desc");
    expect(dialog).toContainElement(document.activeElement as HTMLElement);

    await userEvent.tab();
    await userEvent.tab();
    await userEvent.tab();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    await userEvent.tab({ shift: true });
    await userEvent.tab({ shift: true });
    await userEvent.tab({ shift: true });
    await userEvent.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
  });

  it("closes from the close button and the backdrop", async () => {
    const onClose = vi.fn();
    const { container } = render(
      <Dialog open title="T" onClose={onClose}>
        body
      </Dialog>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    await userEvent.click(
      container.querySelector("[aria-hidden='true'].absolute") as HTMLElement,
    );
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("confirm dialog reports confirm and cancel", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Sure?"
        description="This is permanent."
        confirmLabel="Remove"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onConfirm).toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalled();
  });
});

describe("Menu", () => {
  it("opens, navigates with arrows, selects and closes", async () => {
    const onFirst = vi.fn();
    const onSecond = vi.fn();
    render(
      <Menu
        items={[
          { label: "First", onSelect: onFirst },
          { label: "Second", onSelect: onSecond, checked: true },
        ]}
        trigger={(props) => <button {...props}>Menu</button>}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Menu" }));
    expect(screen.getByRole("menuitem", { name: "First" })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitemradio", { name: /Second/ })).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    await userEvent.keyboard("{Enter}");
    expect(onFirst).toHaveBeenCalled();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes on Escape and on outside click", async () => {
    render(
      <>
        <p>outside</p>
        <Menu
          items={[{ label: "Only", onSelect: vi.fn() }]}
          trigger={(props) => <button {...props}>Menu</button>}
        />
      </>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Menu" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("button", { name: "Menu" })).toHaveFocus();
    await userEvent.click(screen.getByRole("button", { name: "Menu" }));
    await userEvent.click(screen.getByText("outside"));
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

describe("Toast", () => {
  function Trigger() {
    const toast = useToast();
    return <button onClick={() => toast.push("Saved it")}>Go</button>;
  }

  it("announces a toast and auto-dismisses it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(within(screen.getByRole("status")).getByText("Saved it")).toBeVisible();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.queryByText("Saved it")).toBeNull();
    vi.useRealTimers();
  });

  it("can be dismissed manually and is a no-op outside a provider", async () => {
    const { unmount } = render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
    await userEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
    expect(screen.queryByText("Saved it")).toBeNull();
    unmount();
    render(<Trigger />);
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
  });
});

describe("CopyButton and CodeBlock", () => {
  it("copies the text and confirms", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    render(<CodeBlock code="SELECT 1" />);
    expect(screen.getByLabelText("SQL")).toHaveTextContent("SELECT 1");
    await userEvent.click(screen.getByRole("button", { name: "Copy SQL" }));
    expect(writeText).toHaveBeenCalledWith("SELECT 1");
    expect(await screen.findByRole("button", { name: "Copied" })).toBeVisible();
    vi.unstubAllGlobals();
  });

  it("stays quiet when the clipboard is blocked", async () => {
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    render(<CopyButton text="x" />);
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(screen.getByRole("button", { name: "Copy" })).toBeVisible();
    vi.unstubAllGlobals();
  });
});
