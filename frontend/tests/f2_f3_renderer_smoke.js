const path = require("path");
const { app, BrowserWindow } = require("electron");

app.commandLine.appendSwitch("use-fake-ui-for-media-stream");
app.commandLine.appendSwitch("enable-experimental-web-platform-features");

const ROOT = path.resolve(__dirname, "..");

function fail(message) {
  throw new Error(message);
}

async function main() {
  await app.whenReady();

  const win = new BrowserWindow({
    show: false,
    width: 1280,
    height: 900,
    webPreferences: {
      preload: path.join(ROOT, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      webSecurity: false,
      backgroundThrottling: false,
    },
  });

  win.webContents.on("console-message", (_event, _level, message) => {
    if (/^\[(PERF|Smoke|Chat Paste|Attachment Chat)/.test(message)) {
      console.log(`[renderer] ${message}`);
    }
  });

  await win.loadFile(path.join(ROOT, "renderer", "index.html"));

  const result = await win.webContents.executeJavaScript(`
    (async () => {
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const waitFor = async (predicate, label, timeoutMs = 7000) => {
        const start = performance.now();
        while (performance.now() - start < timeoutMs) {
          if (predicate()) return;
          await wait(50);
        }
        throw new Error("Timed out waiting for " + label);
      };
      const assert = (condition, message) => {
        if (!condition) throw new Error(message);
      };
      const paste = (target, dataTransfer) => {
        const event = new Event("paste", { bubbles: true, cancelable: true });
        Object.defineProperty(event, "clipboardData", { value: dataTransfer });
        target.dispatchEvent(event);
        return event;
      };
      const pngDataUrl = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=";

      await waitFor(() => window.SARAH_UI && window.SARAH_UI.projectModal, "SarahUI");
      const ui = window.SARAH_UI;
      window.confirm = () => true;
      window.open = () => null;

      const results = {};

      const messages = Array.from({ length: 200 }, (_, idx) => ({
        id: idx + 1,
        role: idx % 2 === 0 ? "user" : "assistant",
        content: "Synthetic message " + idx + " " + "x".repeat(120),
      }));
      let start = performance.now();
      const fragment = document.createDocumentFragment();
      for (const msg of messages) {
        fragment.appendChild(
          ui._createMessageBubble(msg.role, msg.content, null, { messageId: msg.id })
        );
      }
      ui.chatLog.replaceChildren(fragment);
      ui._scrollToBottom();
      results.conversationRenderMs = performance.now() - start;
      await new Promise((resolve) => requestAnimationFrame(resolve));
      console.log("[Smoke] conversationRenderMs", results.conversationRenderMs.toFixed(1));
      assert(results.conversationRenderMs <= 220, "conversation render exceeded 220ms");

      const projectFiles = Array.from({ length: 500 }, (_, idx) => ({
        id: idx + 1,
        file_name: "file_" + idx + ".js",
        file_path: "src/module_" + (idx % 20) + "/file_" + idx + ".js",
        file_size: 1024 + idx,
      }));
      ui.projectModal.isTreeView = false;
      start = performance.now();
      ui.projectModal.renderFiles(projectFiles);
      results.projectFilesRenderMs = performance.now() - start;
      await new Promise((resolve) => requestAnimationFrame(resolve));
      console.log("[Smoke] projectFilesRenderMs", results.projectFilesRenderMs.toFixed(1));
      assert(results.projectFilesRenderMs <= 180, "project file render exceeded 180ms");

      ui._clearPendingAttachments();
      const input = document.getElementById("chat-input");
      input.value = "";
      const textDt = new DataTransfer();
      textDt.setData("text/plain", "native paste text");
      const textEvent = paste(input, textDt);
      results.textPasteDefaultPrevented = textEvent.defaultPrevented;
      assert(!textEvent.defaultPrevented, "text-only paste should remain native");

      const imageDt = new DataTransfer();
      const imageBlob = await (await fetch(pngDataUrl)).blob();
      imageDt.items.add(new File([imageBlob], "pasted.png", { type: "image/png" }));
      paste(input, imageDt);
      await waitFor(() => ui.pendingAttachments.length === 1, "image attachment queue");
      assert(ui.pendingAttachments[0].kind === "image", "image paste did not queue image attachment");
      assert(!document.getElementById("chat-attachment-preview").classList.contains("hidden"), "attachment preview hidden after image paste");
      document.querySelector(".chat-attachment-remove").click();
      await waitFor(() => ui.pendingAttachments.length === 0, "attachment remove");

      const fileDt = new DataTransfer();
      fileDt.items.add(new File(["console.log('hello');"], "snippet.js", { type: "text/plain" }));
      paste(input, fileDt);
      await waitFor(() => ui.pendingAttachments.length === 1, "text file attachment queue");
      assert(ui.pendingAttachments[0].kind === "text", "text file paste did not queue text attachment");

      let capturedTextMessage = "";
      ui._sendChat = async (message) => {
        capturedTextMessage = message;
      };
      input.value = "Review this snippet";
      await ui.sendChatFromInput();
      assert(capturedTextMessage.includes("[Text Attachment: snippet.js]"), "text attachment was not folded into chat message");
      assert(ui.pendingAttachments.length === 0, "text attachment was not cleared after send");

      let capturedImageMessage = "";
      ui.activeConversationId = 1;
      ui._ensureActiveConversation = async () => {};
      ui._analyzeImageAttachment = async () => "mock visual summary";
      ui.backend.chat = async (message) => {
        capturedImageMessage = message;
        return {
          reply: "mock assistant reply",
          user_message_id: 1001,
          assistant_message_id: 1002,
          tokens_used: 10,
          token_budget: 5900,
        };
      };
      ui.tts.isEnabled = () => false;
      ui.syncMoodFromBackend = async () => {};
      ui._addPendingAttachment({
        kind: "image",
        name: "queued.png",
        type: "image/png",
        size: imageBlob.size,
        dataUrl: pngDataUrl,
      });
      await ui.sendChatFromInput();
      assert(capturedImageMessage.includes("[Image Attachment: queued.png]"), "image attachment was not folded into chat message");
      assert(capturedImageMessage.includes("mock visual summary"), "image visual summary missing from chat message");
      assert(ui.pendingAttachments.length === 0, "image attachment was not cleared after send");

      return results;
    })();
  `, true);

  console.log(JSON.stringify({ ok: true, ...result }, null, 2));
  await win.close();
}

main()
  .catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exitCode = 1;
  })
  .finally(() => app.quit());
