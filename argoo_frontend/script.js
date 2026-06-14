const BACKEND_URL = window.BACKEND_URL || "https://argo-rn2r.onrender.com";

// =================== FILL BOTH INPUTS ===================
function fillBoth(text) {
  document.getElementById("inputOld").value = text;
  document.getElementById("inputNew").value = text;

  document.querySelectorAll(".query-chip").forEach(c => c.classList.remove("active"));
  event.currentTarget.classList.add("active");

  document.getElementById("chat").scrollIntoView({ behavior: "smooth", block: "start" });
}

// =================== SEND TO BOTH ===================
function sendBoth() {
  const q = document.getElementById("inputOld").value.trim()
         || document.getElementById("inputNew").value.trim();
  if (!q) return;
  document.getElementById("inputOld").value = q;
  document.getElementById("inputNew").value = q;
  sendChat("old");
  sendChat("new");
}

// =================== CLEAR CHAT ===================
function clearChat(type) {
  const chatId  = type === "old" ? "chatOld"  : "chatNew";
  const statsId = type === "old" ? "statsOld" : "statsNew";
  document.getElementById(chatId).innerHTML =
    '<div class="empty-state">Ask me about ocean data...</div>';
  document.getElementById(statsId).querySelector(".stats-content").textContent =
    "No stats yet — send a message first";
}

// =================== SEND CHAT ===================
function sendChat(type) {
  const inputEl   = document.getElementById(type === "old" ? "inputOld"  : "inputNew");
  const chatEl    = document.getElementById(type === "old" ? "chatOld"   : "chatNew");
  const statsEl   = document.getElementById(type === "old" ? "statsOld"  : "statsNew")
                               .querySelector(".stats-content");
  const endpoint  = type === "old" ? "/chat" : "/chat_optimised";

  const question = inputEl.value.trim();
  if (!question) return;

  // Remove placeholder
  const empty = chatEl.querySelector(".empty-state");
  if (empty) empty.remove();

  // User bubble
  const userMsg = document.createElement("div");
  userMsg.className = "msg-user";
  userMsg.textContent = question;
  chatEl.appendChild(userMsg);
  inputEl.value = "";
  chatEl.scrollTop = chatEl.scrollHeight;

  // Typing indicator
  const loading = document.createElement("div");
  loading.className = "msg-loading";
  loading.innerHTML = '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';
  chatEl.appendChild(loading);
  chatEl.scrollTop = chatEl.scrollHeight;

  const startTime = performance.now();

  fetch(`${BACKEND_URL}${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question })
  })
    .then(res => res.json())
    .then(data => {
      loading.remove();

      if (data.error) {
        const err = document.createElement("div");
        err.className = "msg-error";
        err.textContent = "⚠️ " + data.error;
        chatEl.appendChild(err);
      } else {
        const bot = document.createElement("div");
        bot.className = "msg-bot";
        bot.textContent = "🌊 " + data.answer;
        chatEl.appendChild(bot);
      }

      chatEl.scrollTop = chatEl.scrollHeight;

      // Build stats display
      if (data.timing) {
        const t = data.timing;
        const intentHit = t.cache_hit || t.intent_ms < 5;
        const sqlHit    = t.sql_ms < 1;

        const lines = [
          `⏱  Intent :  ${String(t.intent_ms).padStart(8)} ms  ${intentHit ? "→ CACHE HIT 🎯" : "→ LLM call"}`,
          `🗄  SQL    :  ${String(t.sql_ms).padStart(8)} ms  ${sqlHit     ? "→ CACHE HIT 🎯" : "→ DB query"}`,
          `⚡  Total  :  ${String(t.total_ms).padStart(8)} ms`,
          t.cache_hit ? `\n✅ Full cache hit — zero LLM calls, zero DB hits` : "",
          `\n── Generated SQL ──`,
          data.sql || "n/a"
        ].filter(Boolean);

        statsEl.textContent = lines.join("\n");
      }
    })
    .catch(() => {
      loading.remove();
      const err = document.createElement("div");
      err.className = "msg-error";
      err.textContent = "⚠️ Connection error — is the backend running?";
      chatEl.appendChild(err);
      chatEl.scrollTop = chatEl.scrollHeight;
    });
}
