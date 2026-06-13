const BACKEND_URL = "http://127.0.0.1:8000";

/* =========================
   REFRESH DATA (unchanged)
========================= */
document.getElementById("refreshBtn").onclick = async () => {
  const btn = document.getElementById("refreshBtn");
  const status = document.getElementById("refreshStatus");

  btn.disabled = true;
  btn.innerText = "⏳ Updating...";
  status.innerText = "";

  try {
    const res = await fetch(`${BACKEND_URL}/refresh_data`, {
      method: "POST"
    });

    if (res.ok) {
      const data = await res.json();
      status.innerText = data.message;
      status.style.color = "green";
      setTimeout(() => status.innerText = "", 3000);
    }
  } catch {
    status.innerText = "⚠️ Refresh failed";
    status.style.color = "red";
    setTimeout(() => status.innerText = "", 3000);
  }

  btn.disabled = false;
  btn.innerText = "🔄 Refresh Data";
};


/* =========================
   CHAT WITH STATS
========================= */
function sendChat(type) {

  const input = type === "old"
    ? document.getElementById("inputOld")
    : document.getElementById("inputNew");

  const chatBox = type === "old"
    ? document.getElementById("chatOld")
    : document.getElementById("chatNew");

  const statsBox = type === "old"
    ? document.getElementById("statsOld").querySelector(".stats-content")
    : document.getElementById("statsNew").querySelector(".stats-content");

  const endpoint = type === "old"
    ? "/chat"
    : "/chat_optimised";

  const question = input.value.trim();
  if (!question) return;

  // show user message
  chatBox.innerHTML += `<div><b>You:</b> ${question}</div>`;
  input.value = "";
  chatBox.scrollTop = chatBox.scrollHeight;

  fetch(`${BACKEND_URL}${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question })
  })
    .then(res => res.json())
    .then(data => {

      // show answer
      chatBox.innerHTML += `<div><b>Bot:</b> ${data.answer}</div>`;
      chatBox.scrollTop = chatBox.scrollHeight;

      // 🔥 BUILD STATS VIEW
      const stats = {
        timing: data.timing,
        sql: data.sql
      };

      statsBox.textContent = JSON.stringify(stats, null, 2);
    })
    .catch(() => {
      chatBox.innerHTML += `<div style="color:red;"><b>Bot:</b> Error</div>`;
    });
}
