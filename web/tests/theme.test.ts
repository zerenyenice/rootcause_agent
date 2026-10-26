import {
  THEME_STORAGE_KEY,
  applyTheme,
  isThemePreference,
  readStoredTheme,
  storeTheme,
} from "@/lib/theme";

function mockSystemDark(isDark: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches: isDark, addEventListener: vi.fn() }),
  );
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.classList.remove("dark");
});
afterEach(() => vi.unstubAllGlobals());

describe("theme", () => {
  it("validates preferences", () => {
    expect(isThemePreference("dark")).toBe(true);
    expect(isThemePreference("blue")).toBe(false);
  });

  it("round-trips the stored preference and defaults to system", () => {
    expect(readStoredTheme()).toBe("system");
    storeTheme("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(readStoredTheme()).toBe("dark");
    window.localStorage.setItem(THEME_STORAGE_KEY, "garbage");
    expect(readStoredTheme()).toBe("system");
  });

  it("toggles the dark class, following the OS in system mode", () => {
    mockSystemDark(true);
    applyTheme("system");
    expect(document.documentElement).toHaveClass("dark");
    applyTheme("light");
    expect(document.documentElement).not.toHaveClass("dark");
    applyTheme("dark");
    expect(document.documentElement).toHaveClass("dark");
    mockSystemDark(false);
    applyTheme("system");
    expect(document.documentElement).not.toHaveClass("dark");
  });
});
