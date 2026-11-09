// Small, testable helpers shared by the UI.

export async function loadConfig(fetchFn = fetch) {
  try {
    const res = await fetchFn("config.json", { cache: "no-store" });
    if (res.ok) return await res.json();
  } catch (_) { /* fall back to the default below */ }
  return { apiBase: "/api" };
}

export function parseOptions(text) {
  return text.split("\n").map((s) => s.trim()).filter(Boolean);
}

export function voterId(storage = localStorage) {
  let id = storage.getItem("pollie-voter");
  if (!id) {
    id = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)) + "";
    storage.setItem("pollie-voter", id);
  }
  return id;
}

export function percentBar(result) {
  return `${result.percent}%`;
}
