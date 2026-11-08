const USER_ID = localStorage.getItem("devUserId") ?? "1"; // the auth proxy sets this header in prod
const api = (path, opts = {}) =>
  fetch(`/api/todos${path}`, { ...opts, headers: { "Content-Type": "application/json", "X-User-Id": USER_ID } });

async function render() {
  const todos = await (await api("/")).json();
  const list = document.querySelector("#todo-list");
  list.innerHTML = "";
  for (const t of todos) {
    const li = document.createElement("li");
    li.className = t.done ? "done" : "";
    li.innerHTML = `<label><input type="checkbox" ${t.done ? "checked" : ""}> <span></span></label>
      <button class="delete" aria-label="Delete">x</button>`;
    li.querySelector("span").textContent = t.title;
    li.querySelector("input").onchange = (e) =>
      api(`/${t.id}`, { method: "PATCH", body: JSON.stringify({ done: e.target.checked }) }).then(render);
    li.querySelector(".delete").onclick = () => api(`/${t.id}`, { method: "DELETE" }).then(render);
    list.append(li);
  }
}

document.querySelector("#new-todo").onsubmit = async (e) => {
  e.preventDefault();
  const title = new FormData(e.target).get("title");
  await api("/", { method: "POST", body: JSON.stringify({ title }) });
  e.target.reset();
  render();
};

render();
