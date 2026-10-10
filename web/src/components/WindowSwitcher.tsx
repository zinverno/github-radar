import type { WindowDays } from "../lib/types";

const OPTIONS: Array<{ value: WindowDays; label: string }> = [
  { value: "1d", label: "1d" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
];

export function WindowSwitcher({
  value,
  onChange,
}: {
  value: WindowDays;
  onChange: (value: WindowDays) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label="Analysis window">
      {OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`segment${value === option.value ? " is-active" : ""}`}
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}