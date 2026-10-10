import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfidencePill } from "../confidence";
import { WindowSwitcher } from "../WindowSwitcher";

describe("ConfidencePill", () => {
  it("renders the level", () => {
    render(<ConfidencePill level="HIGH" />);
    expect(screen.getByText("HIGH")).toBeInTheDocument();
    expect(screen.getByText("HIGH")).toHaveClass("pill-high");
  });
});

describe("WindowSwitcher", () => {
  it("reports the selected window", async () => {
    const onChange = vi.fn();
    render(<WindowSwitcher value="7d" onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "30d" }));
    expect(onChange).toHaveBeenCalledWith("30d");
    expect(screen.getByRole("button", { name: "7d" })).toHaveAttribute("aria-pressed", "true");
  });
});