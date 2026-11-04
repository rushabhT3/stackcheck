export type Route =
  | { name: "start" }
  | { name: "check"; id: string }
  | { name: "report"; id: string }
  | { name: "accuracy" };

function parseHash(): Route {
  const hash = window.location.hash || "#/";
  const check = hash.match(/^#\/check\/([\w-]+)\/?$/);
  if (check) return { name: "check", id: check[1] };
  const report = hash.match(/^#\/report\/([\w-]+)\/?$/);
  if (report) return { name: "report", id: report[1] };
  if (hash === "#/accuracy" || hash === "#/accuracy/") return { name: "accuracy" };
  return { name: "start" };
}

export const router = $state({ route: parseHash() });

if (typeof window !== "undefined") {
  window.addEventListener("hashchange", () => {
    router.route = parseHash();
  });
}

export function navigate(hash: string): void {
  window.location.hash = hash;
}
