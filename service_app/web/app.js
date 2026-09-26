(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const el = {
    serverUrl:$("serverUrl"),model:$("model"),connect:$("connectButton"),state:$("connectionState"),
    session:$("sessionMetric"),latency:$("latencyMetric"),audioUp:$("audioUpMetric"),
    audioDown:$("audioDownMetric"),events:$("eventMetric"),timeline:$("timeline"),empty:$("emptyState"),
    mic:$("micButton"),micLabel:$("micLabel"),micHint:$("micHint"),inputLevel:$("inputLevel"),
    commit:$("commitButton"),speaker:$("speakerButton"),eventList:$("eventList"),
    eventFilter:$("eventFilter"),rawEvent:$("rawEvent"),sendRaw:$("sendRawButton"),
    instructions:$("instructions"),toolsJson:$("toolsJson"),updateSession:$("updateSessionButton"),
    toolCalls:$("toolCalls"),toolEmpty:$("toolEmpty"),toolBadge:$("toolBadge"),toasts:$("toastRegion")
  };
  const state = {
    ws:null,connectedAt:0,firstResponseAt:0,audioContext:null,mediaStream:null,
    processor:null,source:null,microphoneOn:false,muted:false,clockTimer:null,
    captureSamples:[],capturePackets:[],nextPlaybackAt:0,eventCount:0,
    audioUpBytes:0,audioDownBytes:0,micFrames:0,micPacketsSent:0,
    messages:new Map(),pendingTools:new Map()
  };

  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  el.serverUrl.value = `${protocol}//${location.host}/v1/realtime`;

  function setConnection(kind, text) {
    el.state.dataset.state = kind;
    el.state.querySelector("span").textContent = text;
  }
  function setControls(connected) {
    el.connect.querySelector("span:last-child").textContent = connected ? "Disconnect" : "Connect";
    [el.mic, el.commit, el.sendRaw, el.updateSession].forEach((node) => { node.disabled = !connected; });
  }
  function toast(message, error = false) {
    const node = document.createElement("div");
    node.className = `toast${error ? " error" : ""}`;
    node.textContent = message;
    el.toasts.append(node);
    setTimeout(() => node.remove(), 4200);
  }
  function displayEvent(event) {
    const copy = JSON.parse(JSON.stringify(event));
    if (typeof copy.audio === "string") copy.audio = `<base64 audio · ${copy.audio.length} chars>`;
    if (event.type === "response.output_audio.delta" && typeof copy.delta === "string") {
      copy.delta = `<base64 audio · ${copy.delta.length} chars>`;
    }
    return copy;
  }
  function logEvent(direction, event) {
    state.eventCount += 1;
    el.events.textContent = String(state.eventCount);
    const row = document.createElement("div");
    row.className = "event-row";
    row.dataset.direction = direction;
    row.dataset.error = String(event.type === "error");
    row.innerHTML = `<time>${new Date().toLocaleTimeString([], {hour12:false})}</time><span class="direction">${direction === "server" ? "↓" : "↑"}</span><span class="event-type"></span><pre></pre>`;
    row.querySelector(".event-type").textContent = event.type || "unknown";
    row.querySelector("pre").textContent = JSON.stringify(displayEvent(event), null, 2);
    row.addEventListener("click", () => row.classList.toggle("expanded"));
    el.eventList.append(row);
    filterEvents();
    el.eventList.scrollTop = el.eventList.scrollHeight;
    while (el.eventList.children.length > 500) el.eventList.firstElementChild.remove();
  }
  function filterEvents() {
    const filter = el.eventFilter.value;
    [...el.eventList.children].forEach((row) => {
      row.hidden = filter !== "all" && (filter === "error" ? row.dataset.error !== "true" : row.dataset.direction !== filter);
    });
  }
  function send(event, quiet = false) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
      if (!quiet) toast("WebSocket is not connected", true);
      return false;
    }
    state.ws.send(JSON.stringify(event));
    if (!quiet) logEvent("client", event);
    return true;
  }
  function socketUrl() {
    const url = new URL(el.serverUrl.value.trim());
    if (!/^wss?:$/.test(url.protocol)) throw new Error("Endpoint must use ws:// or wss://");
    if (el.model.value.trim()) url.searchParams.set("model", el.model.value.trim());
    return url.toString();
  }

  function connect() {
    if (state.ws && [WebSocket.OPEN, WebSocket.CONNECTING].includes(state.ws.readyState)) {
      state.ws.close(1000, "Closed from debugger");
      return;
    }
    let url;
    try { url = socketUrl(); } catch (error) { toast(error.message, true); return; }
    // Ask for the microphone from the Connect click, while user activation is valid.
    startMicrophone();
    setConnection("connecting", "CONNECTING");
    el.connect.disabled = true;
    const ws = new WebSocket(url);
    state.ws = ws;
    ws.addEventListener("open", async () => {
      el.connect.disabled = false;
      state.connectedAt = performance.now();
      state.firstResponseAt = 0;
      setConnection("online", "ONLINE");
      setControls(true);
      await ensureAudioContext();
      startAudioClock();
      updateSession();
      toast("Realtime connection established");
    });
    ws.addEventListener("message", (message) => {
      let event;
      try { event = JSON.parse(message.data); } catch { toast("Server returned invalid JSON", true); return; }
      logEvent("server", event);
      handleEvent(event);
    });
    ws.addEventListener("error", () => toast("WebSocket connection error", true));
    ws.addEventListener("close", (event) => {
      el.connect.disabled = false;
      if (state.ws === ws) state.ws = null;
      stopAudioClock();
      stopMicrophone();
      setConnection("offline", "OFFLINE");
      setControls(false);
      el.session.textContent = "—";
      if (event.code !== 1000) toast(`Connection closed (${event.code || "no code"})`, true);
    });
  }

  function updateSession() {
    let tools;
    try {
      tools = JSON.parse(el.toolsJson.value || "[]");
      if (!Array.isArray(tools)) throw new Error("Tools must be a JSON array");
    } catch (error) { toast(`Invalid tools JSON: ${error.message}`, true); return; }
    send({type:"session.update", session:{instructions:el.instructions.value, tools}});
  }

  function handleEvent(event) {
    if (!state.firstResponseAt && event.type.startsWith("response.")) {
      state.firstResponseAt = performance.now();
      el.latency.textContent = `${Math.round(state.firstResponseAt - state.connectedAt)}ms`;
    }
    switch (event.type) {
      case "session.created":
        el.session.textContent = event.session?.id || "local";
        break;
      case "session.updated":
        toast("Session configuration accepted");
        break;
      case "conversation.item.input_audio_transcription.completed":
        if (event.transcript) addMessage("user", event.item_id, event.transcript, false);
        break;
      case "response.text.delta":
        appendAssistant(event.item_id, event.delta || "");
        break;
      case "response.text.done":
        finishAssistant(event.item_id, event.text);
        break;
      case "response.cancelled":
        markInterrupted();
        break;
      case "response.output_audio.delta":
        receiveAudio(event.delta);
        break;
      case "response.function_call_arguments.done":
        addToolCall(event);
        break;
      case "error":
        addMessage("system", event.event_id, `${event.error?.code || "error"}: ${event.error?.message || "Unknown server error"}`, false);
        toast(event.error?.message || "Server error", true);
        break;
    }
  }

  function revealTimeline() { el.empty.style.display = "none"; }
  function addMessage(role, id, text, streaming) {
    revealTimeline();
    const message = document.createElement("article");
    message.className = `message ${role}${streaming ? " streaming" : ""}`;
    message.dataset.id = id || crypto.randomUUID();
    const short = role === "user" ? "YOU" : role === "assistant" ? "AI" : "SYS";
    const title = role === "user" ? "You" : role === "assistant" ? "Assistant" : "System";
    message.innerHTML = `<div class="avatar">${short}</div><div><div class="message-head"><strong>${title}</strong><time>${new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}</time><em></em></div><div class="message-body"></div></div>`;
    message.querySelector(".message-body").textContent = text || "";
    el.timeline.append(message);
    el.timeline.scrollTop = el.timeline.scrollHeight;
    if (id) state.messages.set(id, message);
    return message;
  }
  function appendAssistant(id, delta) {
    let message = state.messages.get(id);
    if (!message) message = addMessage("assistant", id, "", true);
    message.querySelector(".message-body").textContent += delta;
    el.timeline.scrollTop = el.timeline.scrollHeight;
  }
  function finishAssistant(id, text) {
    let message = state.messages.get(id);
    if (!message) message = addMessage("assistant", id, text || "", false);
    if (text) message.querySelector(".message-body").textContent = text;
    message.classList.remove("streaming");
  }
  function markInterrupted() {
    const message = [...el.timeline.querySelectorAll(".message.assistant")].at(-1);
    if (!message) return;
    message.classList.remove("streaming");
    message.classList.add("interrupted");
    message.querySelector("em").textContent = "INTERRUPTED";
  }

  function addToolCall(event) {
    el.toolEmpty.style.display = "none";
    el.toolBadge.classList.add("visible");
    const card = document.createElement("section");
    card.className = "tool-card";
    card.dataset.callId = event.call_id;
    let pretty = event.arguments || "{}";
    try { pretty = JSON.stringify(JSON.parse(pretty), null, 2); } catch { /* raw arguments */ }
    let args = {};
    try { args = JSON.parse(event.arguments || "{}"); } catch { /* no suggestion */ }
    const suggested = event.name === "get_current_time"
      ? JSON.stringify({city:args.city || "unknown", local_time:new Date().toLocaleString()}, null, 2)
      : JSON.stringify({ok:true}, null, 2);
    card.innerHTML = '<h3></h3><code></code><textarea spellcheck="false"></textarea><button class="button secondary full">Submit result</button>';
    card.querySelector("h3").textContent = event.name || "function";
    card.querySelector("code").textContent = pretty;
    card.querySelector("textarea").value = suggested;
    card.querySelector("button").addEventListener("click", () => submitTool(card, event.call_id));
    el.toolCalls.append(card);
    state.pendingTools.set(event.call_id, card);
    showTab("tools");
  }
  function submitTool(card, callId) {
    const output = card.querySelector("textarea").value;
    if (!send({type:"conversation.item.create", item:{type:"function_call_output", call_id:callId, output}})) return;
    send({type:"response.create"});
    card.classList.add("done");
    card.querySelector("button").disabled = true;
    card.querySelector("button").textContent = "Result submitted";
    state.pendingTools.delete(callId);
    if (!state.pendingTools.size) el.toolBadge.classList.remove("visible");
  }

  function ensureAudioContext() {
    if (!state.audioContext || state.audioContext.state === "closed") {
      state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    return state.audioContext.resume();
  }
  async function toggleMicrophone() {
    if (state.microphoneOn) {
      stopMicrophone();
      send({type:"input_audio_buffer.commit"});
      return;
    }
    await startMicrophone();
  }

  async function startMicrophone() {
    if (state.microphoneOn) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      const reason = "麦克风不可用：请通过 HTTPS 或 http://localhost 打开页面";
      el.micLabel.textContent = "Microphone unavailable";
      el.micHint.textContent = "HTTPS / localhost required";
      toast(reason, true);
      addMessage("system", null, reason, false);
      return;
    }
    try {
      await ensureAudioContext();
      state.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}
      });
      state.micFrames = 0;
      state.micPacketsSent = 0;
      state.source = state.audioContext.createMediaStreamSource(state.mediaStream);
      state.processor = state.audioContext.createScriptProcessor(2048, 1, 1);
      const silentGain = state.audioContext.createGain();
      silentGain.gain.value = 0;
      state.processor.onaudioprocess = captureAudio;
      state.source.connect(state.processor);
      state.processor.connect(silentGain);
      silentGain.connect(state.audioContext.destination);
      state.microphoneOn = true;
      el.mic.classList.add("active");
      el.micLabel.textContent = "Microphone live";
      el.micHint.textContent = "Waiting for audio frames…";
      setTimeout(() => {
        if (state.microphoneOn && state.micFrames === 0) {
          const reason = "麦克风已授权，但浏览器没有产生音频帧";
          el.micHint.textContent = reason;
          toast(reason, true);
        }
      }, 2500);
    } catch (error) {
      const reason = `无法访问麦克风：${error.message}`;
      el.micLabel.textContent = "Microphone blocked";
      el.micHint.textContent = "检查浏览器站点权限";
      toast(reason, true);
      addMessage("system", null, reason, false);
    }
  }
  function stopMicrophone() {
    state.microphoneOn = false;
    state.captureSamples = [];
    state.capturePackets = [];
    if (state.processor) { state.processor.disconnect(); state.processor.onaudioprocess = null; }
    if (state.source) state.source.disconnect();
    if (state.mediaStream) state.mediaStream.getTracks().forEach((track) => track.stop());
    state.processor = null; state.source = null; state.mediaStream = null;
    el.mic.classList.remove("active");
    el.micLabel.textContent = "Microphone off";
    el.micHint.textContent = state.ws ? "音频时钟仍以静音包运行" : "连接后点击开始说话";
    el.inputLevel.style.width = "0";
  }
  function captureAudio(event) {
    if (!state.microphoneOn) return;
    state.micFrames += 1;
    const input = event.inputBuffer.getChannelData(0);
    let sum = 0;
    for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
    el.inputLevel.style.width = `${Math.min(100, Math.sqrt(sum / input.length) * 260)}%`;
    el.micHint.textContent = `Capturing · ${state.audioContext.sampleRate} Hz source`;
    const samples = resample(input, state.audioContext.sampleRate, 8000);
    for (let i = 0; i < samples.length; i++) state.captureSamples.push(samples[i]);
    while (state.captureSamples.length >= 1600) {
      const frame = state.captureSamples.splice(0, 1600);
      const encoded = new Uint8Array(1600);
      for (let i = 0; i < frame.length; i++) encoded[i] = linearToUlaw(frame[i]);
      state.capturePackets.push(encoded);
    }
  }
  function resample(input, fromRate, toRate) {
    if (fromRate === toRate) return input;
    const ratio = fromRate / toRate;
    const output = new Float32Array(Math.floor(input.length / ratio));
    for (let i = 0; i < output.length; i++) {
      const position = i * ratio;
      const left = Math.floor(position);
      const fraction = position - left;
      output[i] = input[left] * (1 - fraction) + input[Math.min(left + 1, input.length - 1)] * fraction;
    }
    return output;
  }
  function linearToUlaw(value) {
    const BIAS = 0x84;
    let sample = Math.round(Math.max(-1, Math.min(1, value)) * 32767);
    const sign = sample < 0 ? 0x80 : 0;
    if (sample < 0) sample = -sample;
    sample = Math.min(32635, sample) + BIAS;
    let exponent = 7;
    for (let mask = 0x4000; exponent > 0 && !(sample & mask); exponent--, mask >>= 1) {}
    const mantissa = (sample >> (exponent + 3)) & 0x0f;
    return (~(sign | (exponent << 4) | mantissa)) & 0xff;
  }
  function ulawToLinear(byte) {
    byte = (~byte) & 0xff;
    const sign = byte & 0x80;
    const exponent = (byte >> 4) & 7;
    const mantissa = byte & 15;
    let sample = ((mantissa << 3) + 0x84) << exponent;
    sample -= 0x84;
    return (sign ? -sample : sample) / 32768;
  }
  function toBase64(bytes) {
    let binary = "";
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  }
  function fromBase64(value) {
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }
  function startAudioClock() {
    stopAudioClock();
    const silence = new Uint8Array(1600);
    silence.fill(0xff);
    const tick = () => {
      if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
      const hasMicPacket = state.microphoneOn && state.capturePackets.length > 0;
      const packet = hasMicPacket ? state.capturePackets.shift() : silence;
      if (send({type:"input_audio_buffer.append", audio:toBase64(packet)}, true)) {
        state.audioUpBytes += packet.length;
        el.audioUp.textContent = `${(state.audioUpBytes / 8000).toFixed(1)}s`;
        if (hasMicPacket) {
          state.micPacketsSent += 1;
          el.micHint.textContent = `Live · ${state.micPacketsSent} voice packets sent`;
        }
      }
    };
    tick();
    state.clockTimer = setInterval(tick, 200);
  }
  function stopAudioClock() {
    if (state.clockTimer) clearInterval(state.clockTimer);
    state.clockTimer = null;
  }
  async function receiveAudio(base64) {
    if (!base64) return;
    await ensureAudioContext();
    const bytes = fromBase64(base64);
    state.audioDownBytes += bytes.length;
    el.audioDown.textContent = `${(state.audioDownBytes / 8000).toFixed(1)}s`;
    if (state.muted) return;
    const buffer = state.audioContext.createBuffer(1, bytes.length, 8000);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < bytes.length; i++) channel[i] = ulawToLinear(bytes[i]);
    const source = state.audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(state.audioContext.destination);
    const startAt = Math.max(state.audioContext.currentTime + 0.025, state.nextPlaybackAt);
    source.start(startAt);
    state.nextPlaybackAt = startAt + buffer.duration;
  }
  function toggleMute() {
    state.muted = !state.muted;
    el.speaker.setAttribute("aria-pressed", String(state.muted));
    el.speaker.textContent = state.muted ? "Muted" : "Sound on";
  }
  function sendRaw() {
    try { send(JSON.parse(el.rawEvent.value)); }
    catch (error) { toast(`Invalid JSON: ${error.message}`, true); }
  }
  function showTab(name) {
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
    document.querySelectorAll(".tab-view").forEach((view) => view.classList.remove("active"));
    $(`${name}Tab`).classList.add("active");
  }

  el.connect.addEventListener("click", connect);
  el.mic.addEventListener("click", toggleMicrophone);
  el.commit.addEventListener("click", () => send({type:"input_audio_buffer.commit"}));
  el.speaker.addEventListener("click", toggleMute);
  el.updateSession.addEventListener("click", updateSession);
  el.sendRaw.addEventListener("click", sendRaw);
  el.eventFilter.addEventListener("change", filterEvents);
  $("clearEvents").addEventListener("click", () => { el.eventList.textContent = ""; });
  $("clearConversation").addEventListener("click", () => {
    el.timeline.querySelectorAll(".message").forEach((message) => message.remove());
    state.messages.clear();
    el.empty.style.display = "flex";
  });
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => showTab(tab.dataset.tab)));
  window.addEventListener("beforeunload", () => { if (state.ws) state.ws.close(); stopMicrophone(); });
})();
