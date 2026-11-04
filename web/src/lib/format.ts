const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** Format an ISO date like "2025-03-31" as "31 Mar 2025". */
export function formatDate(iso: string | null): string {
  if (!iso) return "Not reported";
  const parts = iso.split("-").map(Number);
  if (parts.length !== 3 || parts.some((n) => !Number.isFinite(n))) return iso;
  const [year, month, day] = parts;
  if (month < 1 || month > 12) return iso;
  return `${day} ${MONTHS[month - 1]} ${year}`;
}

/** Format a Decimal-string rupee value with Indian digit grouping, e.g. "₹8,82,41,803". */
export function formatInr(value: string | null): string {
  if (value === null || value === "") return "Not reported";
  const [intPart] = value.split(".");
  const digits = intPart.replace(/[^0-9]/g, "");
  if (digits === "") return value;
  const tail = digits.slice(-3);
  const head = digits.slice(0, -3).replace(/(\d)(?=(\d{2})+$)/g, "$1,");
  return `₹${head === "" ? tail : `${head},${tail}`}`;
}

export function statusLabel(status: string): string {
  if (status === "CANT_VERIFY") return "Can't verify";
  return status;
}

/** Mirror of the Python to_inr: lakh x 100,000, crore x 10,000,000. */
export function toInrNumber(value: string | null, unit: string | null): number | null {
  if (value === null || value === "") return null;
  const num = Number(value);
  if (!Number.isFinite(num)) return null;
  const name = (unit ?? "INR").toUpperCase();
  if (name === "INR") return num;
  if (name === "INR_LAKH" || name === "LAKH") return num * 100_000;
  if (name === "INR_CRORE" || name === "CRORE") return num * 10_000_000;
  return null;
}

/** Converted rupees as "Rs 39.9 crore". Unit-aware; "Not reported" when missing. */
export function formatCrore(value: string | null, unit: string | null = "INR"): string {
  const inr = toInrNumber(value, unit);
  if (inr === null) return "Not reported";
  return `Rs ${(inr / 10_000_000).toFixed(1)} crore`;
}
