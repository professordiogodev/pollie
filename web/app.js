import { loadConfig, parseOptions, voterId, percentBar } from "./api.js";

const cfg = await loadConfig();
const api = (path, opts) => fetch(cfg.apiBase + path, { headers: { "Content-Type": "application/json" }, ...opts });
const list = document.getElementById("poll-list");

async function checkApi() {
  try {
    const res = await api("/healthz");
    const body = await res.json();
    document.getElementById("status").textContent = res.ok ? "API ok" : "API problem";
    document.getElementById("status").className = res.ok ? "ok" : "error";
    document.getElementById("version").textContent = "api " + body.version;
  } catch (_) {
    document.getElementById("status").textContent = "API unreachable";
    document.getElementById("status").className = "error";
  }
}

function pollCard(poll, results) {
  const div = document.createElement("div");
  div.className = "poll";
  const h = document.createElement("h3");
  h.textContent = poll.question;
  div.append(h);
  const voted = localStorage.getItem("pollie-voted-" + poll.id);
  poll.options.forEach((opt) => {
    const row = document.createElement("div");
    row.className = "option";
    const btn = document.createElement("button");
    btn.textContent = opt.label;
    btn.disabled = Boolean(voted);
    btn.onclick = async () => {
      btn.disabled = true;
      await api(`/polls/${poll.id}/votes`, { method: "POST", body: JSON.stringify({ option_id: opt.id, voter_id: voterId() }) });
      localStorage.setItem("pollie-voted-" + poll.id, "1");
      setTimeout(refresh, 800);
    };
    const bar = document.createElement("div");
    bar.className = "bar";
    const r = results && results.results.find((x) => x.option_id === opt.id);
    bar.innerHTML = `<span style="width:${r ? percentBar(r) : "0%"}"></span><em>${r ? r.votes : 0}</em>`;
    row.append(btn, bar);
    div.append(row);
  });
  return div;
}

async function refresh() {
  try {
    const polls = await (await api("/polls")).json();
    const cards = [];
    for (const poll of polls) {
      const results = await (await api(`/polls/${poll.id}/results`)).json();
      cards.push(pollCard(poll, results));
    }
    list.replaceChildren(...(cards.length ? cards : [Object.assign(document.createElement("p"), { className: "muted", textContent: "No polls yet. Create the first one." })]));
  } catch (_) {
    list.textContent = "Could not load polls.";
  }
}

document.getElementById("create-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("create-error");
  err.hidden = true;
  const res = await api("/polls", { method: "POST", body: JSON.stringify({ question: document.getElementById("question").value, options: parseOptions(document.getElementById("options").value) }) });
  if (!res.ok) {
    err.textContent = (await res.json()).detail || "Could not create the poll";
    err.hidden = false;
    return;
  }
  e.target.reset();
  refresh();
});

checkApi();
refresh();
setInterval(refresh, 5000);
