/**
 * Split pasted PDF URLs on whitespace or commas.
 * Trims each part, drops empties, and dedupes keeping first-seen order.
 */
export function parsePdfUrls(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of text.split(/[\s,]+/)) {
    const url = part.trim();
    if (url === "" || seen.has(url)) continue;
    seen.add(url);
    out.push(url);
  }
  return out;
}
