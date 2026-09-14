const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const messages = document.querySelector("#messages");
const sendButton = document.querySelector("#send-button");

let conversationId = null;
const history = [];

function addBubble(role, text = "") {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  setBubbleText(bubble, text, role);
  messages.appendChild(bubble);
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderAssistantText(text) {
  const escaped = escapeHtml(text);
  return escaped
    .replace(/\*\*([^*\n][\s\S]*?[^*\n])\*\*/g, "<strong>$1</strong>")
    .replace(/^\s*(\d+[.．、])\s+/gm, "<br>$1 ")
    .replace(/\n{3,}/g, "\n\n")
    .replaceAll("\n", "<br>");
}

function setBubbleText(bubble, text, role = "assistant") {
  bubble.dataset.rawText = text;
  if (role === "assistant") {
    bubble.innerHTML = renderAssistantText(text);
  } else {
    bubble.textContent = text;
  }
}

function appendAssistantText(bubble, text) {
  setBubbleText(bubble, `${bubble.dataset.rawText || ""}${text}`, "assistant");
}

function parseSseFrames(buffer) {
  const frames = buffer.split("\n\n");
  return {
    complete: frames.slice(0, -1),
    remainder: frames.at(-1) || "",
  };
}

function readFrame(frame) {
  const lines = frame.split("\n");
  const eventLine = lines.find((line) => line.startsWith("event: "));
  const dataLine = lines.find((line) => line.startsWith("data: "));
  return {
    event: eventLine ? eventLine.slice(7) : "message",
    data: dataLine ? dataLine.slice(6) : "",
  };
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;

  addBubble("user", message);
  history.push({ role: "user", content: message });
  input.value = "";
  input.disabled = true;
  sendButton.disabled = true;
  sendButton.textContent = "发送中";

  const assistantBubble = addBubble("assistant", "");

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: conversationId,
        message,
        history,
      }),
    });

    if (!response.ok || !response.body) {
      throw new Error("Chat request failed");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let hasAssistantToken = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseFrames(buffer);
      buffer = parsed.remainder;

      for (const frame of parsed.complete) {
        const { event, data } = readFrame(frame);
        if (event === "metadata") {
          conversationId = JSON.parse(data).conversation_id;
        } else if (event === "status") {
          const payload = JSON.parse(data);
          if (!hasAssistantToken) {
            setBubbleText(assistantBubble, payload.message || "正在处理...");
            messages.scrollTop = messages.scrollHeight;
          }
        } else if (event === "token") {
          if (!hasAssistantToken) {
            setBubbleText(assistantBubble, "");
            hasAssistantToken = true;
          }
          appendAssistantText(assistantBubble, JSON.parse(data));
          messages.scrollTop = messages.scrollHeight;
        } else if (event === "done") {
          history.push({ role: "assistant", content: assistantBubble.dataset.rawText || "" });
        } else if (event === "error") {
          const payload = JSON.parse(data);
          setBubbleText(assistantBubble, `请求失败：${payload.message}`);
        }
      }
    }
  } catch (error) {
    assistantBubble.textContent = "请求失败，请稍后再试。";
  } finally {
    input.disabled = false;
    sendButton.disabled = false;
    sendButton.textContent = "发送";
    input.focus();
  }
});
