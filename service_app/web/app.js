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
    pause:$("pauseButton"),classifierRecords:$("classifierRecords"),reviewSummary:$("reviewSummary"),
    inputQueue:$("inputQueueMetric"),pauseBuffers:$("pauseBuffers"),
    toolCalls:$("toolCalls"),toolEmpty:$("toolEmpty"),toolBadge:$("toolBadge"),toasts:$("toastRegion")
  };
  const state = {
    ws:null,connectedAt:0,firstResponseAt:0,audioContext:null,mediaStream:null,
    processor:null,source:null,microphoneOn:false,muted:false,clockTimer:null,
    captureSamples:[],capturePackets:[],nextPlaybackAt:0,eventCount:0,
    audioUpBytes:0,audioDownBytes:0,micFrames:0,micPacketsSent:0,
    messages:new Map(),pendingTools:new Map(),paused:false,pausePending:false,classifiers:new Map(),
    heartbeatTimer:null,droppedPackets:0,pauseSnapshot:null,lastCaptureAt:null,
    playbackSources:new Set(),playbackGeneration:0,heartbeatRttMs:null
  };
  const MAX_CAPTURE_PACKETS = 2;
  const MAX_SOCKET_AUDIO_BYTES = 8192;

  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  el.serverUrl.value = `${protocol}//${location.host}/v1/realtime`;

  function setConnection(kind, text) {
    el.state.dataset.state = kind;
    el.state.querySelector("span").textContent = text;
  }
  function setControls(connected) {
    el.connect.querySelector("span:last-child").textContent = connected ? "断开连接" : "Connect";
    [el.mic, el.commit, el.sendRaw, el.updateSession].forEach((node) => { node.disabled = !connected || state.paused; });
    el.pause.disabled = !connected || state.pausePending;
  }
  function toast(message, error = false) {
    const node = document.createElement("div");
    node.className = `toast${error ? " error" : ""}`;
    node.textContent = message;
    el.toasts.append(node);
    setTimeout(() => node.remove(), 4200);
  }
  function displayEvent(event) {
    const copy = JSON.parse(JSON.stringify(event, (key, value) => {
      if (key === "url" && typeof value === "string" && value.startsWith("data:audio/")) {
        return `<audio URL · ${value.length} chars · 在 Audio 标签试听>`;
      }
      return value;
    }));
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
      state.paused = false;
      state.pausePending = false;
      state.droppedPackets = 0;
      state.audioUpBytes = 0;
      state.audioDownBytes = 0;
      state.heartbeatRttMs = null;
      el.pause.textContent = "暂停检查";
      el.pause.setAttribute("aria-pressed", "false");
      clearClassifierRecords();
      el.connect.disabled = false;
      state.connectedAt = performance.now();
      state.firstResponseAt = 0;
      setConnection("online", "ONLINE");
      setControls(true);
      await ensureAudioContext();
      startAudioClock();
      updateSession();
      if (state.heartbeatTimer) clearInterval(state.heartbeatTimer);
      state.heartbeatTimer = setInterval(() => send({type:"debug.ping",client_time_ms:performance.now()}, true), 5000);
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
      if (state.ws !== ws) return;
      el.connect.disabled = false;
      if (state.ws === ws) state.ws = null;
      stopAudioClock();
      if (state.heartbeatTimer) clearInterval(state.heartbeatTimer);
      state.heartbeatTimer = null;
      stopMicrophone();
      stopPlayback();
      state.paused = false;
      state.pausePending = false;
      if (state.audioContext?.state === "suspended") state.audioContext.resume();
      el.pause.textContent = "暂停检查";
      el.pause.setAttribute("aria-pressed", "false");
      setConnection("offline", "OFFLINE");
      setControls(false);
      el.session.textContent = "—";
      logEvent("server", {type:"debug.connection.closed",code:event.code,reason:event.reason || "",was_clean:event.wasClean});
      addMessage("system", null, `连接已关闭：${event.code}${event.reason ? ` · ${event.reason}` : ""}`, false);
      if (event.code !== 1000) toast(`Connection closed (${event.code || "no code"})`, true);
    });
  }

  function updateSession() {
    let tools;
    try {
      tools = JSON.parse(el.toolsJson.value || "[]");
      if (!Array.isArray(tools)) throw new Error("Tools must be a JSON array");
    } catch (error) { toast(`Invalid tools JSON: ${error.message}`, true); return; }
    send({type:"session.update", session:{instructions:el.instructions.value, tools, debug:true, debug_audio_mode:"on_demand"}});
  }

  function handleEvent(event) {
    if (!state.firstResponseAt && event.type.startsWith("response.")) {
      state.firstResponseAt = performance.now();
      el.latency.textContent = `${Math.round(state.firstResponseAt - state.connectedAt)}ms`;
    }
    switch (event.type) {
      case "debug.pong":
        if (typeof event.client_time_ms === "number") {
          state.heartbeatRttMs = Math.round(performance.now() - event.client_time_ms);
          updateInputQueue();
        }
        break;
      case "debug.pause.snapshot":
        state.pauseSnapshot = event;
        el.pauseBuffers.textContent = "";
        const heading = document.createElement("h3");
        heading.textContent = `暂停现场 · 服务端音频时钟 ${(event.audio_clock_ms / 1000).toFixed(1)}s`;
        el.pauseBuffers.append(heading);
        addDebugAudio(el.pauseBuffers, "暂停时累计的候选音频", event.buffers.candidate);
        addDebugAudio(el.pauseBuffers, "暂停时保留的 ignorable 环境音", event.buffers.ignorable);
        addDebugAudio(el.pauseBuffers, "暂停时的打断滑动窗口", event.buffers.interrupt);
        addDebugAudio(el.pauseBuffers, "暂停时的完整度滑动窗口", event.buffers.semantic);
        break;
      case "debug.pause.updated":
        state.pausePending = false;
        state.paused = event.paused;
        el.pause.textContent = state.paused ? "继续运行" : "暂停检查";
        el.pause.setAttribute("aria-pressed", String(state.paused));
        setControls(true);
        setConnection(state.paused ? "paused" : "online", state.paused ? "PAUSED" : "ONLINE");
        if (state.paused) {
          showTab("audio");
          el.micHint.textContent = "已暂停接收 · 可试听判定音频";
        } else {
          state.captureSamples = []; state.capturePackets = [];
          state.lastCaptureAt = performance.now();
          state.mediaStream?.getAudioTracks().forEach((track) => { track.enabled = true; });
          state.audioContext?.resume();
          document.querySelectorAll(".debug-audio audio").forEach((audio) => audio.pause());
        }
        break;
      case "debug.classifier.started":
      case "debug.classifier.predicted":
      case "debug.classifier.finished":
      case "debug.classifier.failed":
      case "debug.classifier.input":
        renderClassifier(event);
        break;
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
      case "input_audio_buffer.speech_started":
      case "response.cancelled":
        stopPlayback();
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
    return state.paused ? Promise.resolve() : state.audioContext.resume();
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
      state.lastCaptureAt = performance.now();
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
    state.lastCaptureAt = null;
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
    if (!state.microphoneOn || state.paused) return;
    state.lastCaptureAt = performance.now();
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
    if (state.capturePackets.length > MAX_CAPTURE_PACKETS) {
      const dropped = state.capturePackets.length - MAX_CAPTURE_PACKETS;
      state.capturePackets.splice(0, dropped);
      state.droppedPackets += dropped;
    }
    updateInputQueue();
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
      if (state.paused || !state.ws || state.ws.readyState !== WebSocket.OPEN) return;
      // Avoid adding seconds of old audio ahead of a pause command in the socket.
      if (state.ws.bufferedAmount >= MAX_SOCKET_AUDIO_BYTES) {
        updateInputQueue(); return;
      }
      // A live microphone already supplies silence in its captured PCM. Adding
      // another silent packet while a 200 ms frame is incomplete advances the
      // server clock ahead of the real microphone and creates playback backlog.
      const captureStalled = state.lastCaptureAt === null || performance.now() - state.lastCaptureAt >= 400;
      const packetCount = state.microphoneOn && !captureStalled ? state.capturePackets.length : Math.max(1, state.capturePackets.length);
      for (let index = 0; index < packetCount; index++) {
        if (state.ws.bufferedAmount >= MAX_SOCKET_AUDIO_BYTES) break;
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
      }
      updateInputQueue();
    };
    tick();
    state.clockTimer = setInterval(tick, 200);
  }
  function stopAudioClock() {
    if (state.clockTimer) clearInterval(state.clockTimer);
    state.clockTimer = null;
  }
  function updateInputQueue() {
    const queuedMs = state.capturePackets.length * 200;
    const socketBytes = state.ws?.bufferedAmount || 0;
    const rtt = state.heartbeatRttMs === null ? "—" : `${state.heartbeatRttMs}ms`;
    const playbackMs = state.audioContext ? Math.max(0, Math.round((state.nextPlaybackAt - state.audioContext.currentTime) * 1000)) : 0;
    el.inputQueue.textContent = `${queuedMs}ms · WS ${socketBytes}B · RTT ${rtt} · 播放队列 ${playbackMs}ms · 丢弃 ${state.droppedPackets} 包`;
  }
  function stopPlayback() {
    state.playbackGeneration += 1;
    for (const source of state.playbackSources) {
      source.stop();
      source.disconnect();
    }
    state.playbackSources.clear();
    state.nextPlaybackAt = 0;
  }
  async function receiveAudio(base64) {
    if (!base64) return;
    const generation = state.playbackGeneration;
    await ensureAudioContext();
    if (generation !== state.playbackGeneration) return;
    const bytes = fromBase64(base64);
    state.audioDownBytes += bytes.length;
    el.audioDown.textContent = `${(state.audioDownBytes / 8000).toFixed(1)}s`;
    if (state.muted) return;
    const buffer = state.audioContext.createBuffer(1, bytes.length, 8000);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < bytes.length; i++) channel[i] = ulawToLinear(bytes[i]);
    const source = state.audioContext.createBufferSource();
    state.playbackSources.add(source);
    source.onended = () => { state.playbackSources.delete(source); source.disconnect(); };
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

  async function togglePause() {
    if (state.pausePending) return;
    state.pausePending = true;
    el.pause.disabled = true;
    const resume = state.paused;
    if (!resume) {
      // Stop capture and the upload clock immediately, before the server ack.
      state.paused = true;
      state.captureSamples = []; state.capturePackets = [];
      state.mediaStream?.getAudioTracks().forEach((track) => { track.enabled = false; });
      el.inputLevel.style.width = "0";
      state.audioContext?.suspend();
      updateInputQueue();
      el.pause.textContent = "暂停确认中…";
    }
    send({type:resume ? "debug.resume" : "debug.pause"});
  }

  function addDebugAudio(parent, title, block) {
    const url = block?.audio_url?.url;
    if (!url || !url.startsWith("data:audio/")) return;
    const section = document.createElement("div");
    section.className = "debug-audio";
    const label = document.createElement("p");
    label.textContent = title;
    const player = document.createElement("audio");
    player.controls = true; player.preload = "none"; player.src = url;
    player.addEventListener("play", () => {
      document.querySelectorAll(".debug-audio audio").forEach((other) => { if (other !== player) other.pause(); });
    });
    section.append(label, player); parent.append(section);
  }

  function clearClassifierRecords() {
    document.querySelectorAll(".debug-audio audio").forEach((audio) => { audio.pause(); audio.removeAttribute("src"); audio.load(); });
    el.pauseBuffers.textContent = ""; state.pauseSnapshot = null;
    state.classifiers.clear(); el.classifierRecords.textContent = "";
    updateReviewSummary();
  }

  function updateReviewSummary() {
    const reviewed = [...state.classifiers.values()].filter((record) => record.expected && record.label);
    const wrong = reviewed.filter((record) => record.expected !== record.label).length;
    el.reviewSummary.textContent = `最近 ${state.classifiers.size} 次 · 已标注 ${reviewed.length} · 不符 ${wrong}`;
  }

  function renderClassifier(event) {
    if (event.type === "debug.classifier.started") {
      const card = document.createElement("article"); card.className = "classifier-card";
      const heading = document.createElement("h3");
      const result = document.createElement("p"); result.className = "classifier-result";
      const outcome = document.createElement("pre");
      const record = {...event, card, heading, result, outcome, label:null, expected:""};
      state.classifiers.set(event.request_id, record);
      heading.textContent = `${event.kind === "interrupt" ? "打断" : "语义完整度"} · 输入时钟 ${(event.audio_clock_ms / 1000).toFixed(1)}s · 输入 ${(event.audio_ms / 1000).toFixed(2)}s`;
      result.textContent = `判断中 · 当前输入 ${(event.audio_ms / 1000).toFixed(2)}s · ${event.model}`;
      card.append(heading, result, outcome);
      record.inputArea = document.createElement("div"); card.append(record.inputArea);
      if (event.messages) {
        renderClassifierInput(record);
      } else {
        const load = document.createElement("button"); load.className = "icon-button";
        load.textContent = "加载本次完整输入与试听音频";
        load.addEventListener("click", () => {
          if (send({type:"debug.classifier.inspect",request_id:event.request_id})) {
            load.disabled = true; load.textContent = "加载中…";
          }
        });
        record.inputArea.append(load);
      }
      const reviewLabel = document.createElement("label"); reviewLabel.textContent = "试听后标注期望标签：";
      const select = document.createElement("select");
      ["", ...event.choices].forEach((label) => {
        const option = document.createElement("option"); option.value = label;
        option.textContent = label || "未标注"; select.append(option);
      });
      select.addEventListener("change", () => {
        record.expected = select.value;
        updateClassifierResult(record); updateReviewSummary();
      });
      reviewLabel.append(select); card.append(reviewLabel);
      const download = document.createElement("button"); download.className = "icon-button"; download.textContent = "导出输入及标注 JSON";
      record.download = download; download.disabled = !record.messages;
      download.addEventListener("click", () => {
        const blob = new Blob([JSON.stringify({request_id:record.request_id,kind:record.kind,model:record.model,
          messages:record.messages,choices:record.choices,buffer_audio_ms:record.buffer_audio_ms,pause_snapshot:state.pauseSnapshot,label:record.label,
          expected:record.expected,status:record.status,error:record.error,audio_clock_ms:record.audio_clock_ms}, null, 2)], {type:"application/json"});
        const url = URL.createObjectURL(blob); const link = document.createElement("a");
        link.href = url; link.download = `${record.request_id}.json`; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      });
      card.append(download); el.classifierRecords.prepend(card);
      while (state.classifiers.size > 20) {
        const key = state.classifiers.keys().next().value;
        const old = state.classifiers.get(key);
        old.card.querySelectorAll("audio").forEach((audio) => { audio.pause(); audio.removeAttribute("src"); audio.load(); });
        old.card.remove(); state.classifiers.delete(key);
      }
    } else {
      const record = state.classifiers.get(event.request_id);
      if (!record) return;
      if (event.type === "debug.classifier.input") {
        if (event.messages) {
          record.messages = event.messages;
          renderClassifierInput(record); record.download.disabled = false;
        } else {
          record.inputArea.textContent = event.error;
        }
        return;
      }
      Object.assign(record, event);
      updateClassifierResult(record);
      if (event.state) record.outcome.textContent = JSON.stringify(event.state, null, 2);
    }
    updateReviewSummary();
  }

  function renderClassifierInput(record) {
    record.inputArea.textContent = "";
    const content = record.messages.at(-1)?.content || [];
    const audioBlocks = Array.isArray(content) ? content.filter((block) => block.type === "audio_url") : [];
    audioBlocks.forEach((block, index) => addDebugAudio(record.inputArea,
      index === audioBlocks.length - 1 ? "本次分类输入音频" : "上一轮用户音频（声音上下文）", block));
    const details = document.createElement("details");
    const summary = document.createElement("summary"); summary.textContent = "完整输入消息与其他音频";
    details.append(summary);
    record.messages.slice(0, -1).forEach((message, index) => {
      if (Array.isArray(message.content)) message.content.forEach((block) => addDebugAudio(details, `历史消息 ${index} · ${message.role}`, block));
    });
    const json = document.createElement("pre");
    json.textContent = JSON.stringify(displayEvent({messages:record.messages,choices:record.choices}), null, 2);
    details.append(json); record.inputArea.append(details);
  }

  function updateClassifierResult(record) {
    const review = record.expected && record.label ? (record.expected === record.label ? " · 符合预期" : ` · 不符（期望 ${record.expected}）`) : " · 未标注";
    record.result.textContent = `${record.label || (record.error ? "请求失败" : "判断中")} · ${record.latency_ms ?? "—"}ms · ${record.status || (record.awaiting_resume ? "暂停，待应用" : "待应用")}${review}${record.error ? ` · ${record.error}` : ""}`;
    record.card.dataset.review = !record.expected || !record.label ? "unknown" : record.expected === record.label ? "correct" : "wrong";
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
  el.pause.addEventListener("click", togglePause);
  $("clearClassifiers").addEventListener("click", clearClassifierRecords);
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
  window.addEventListener("beforeunload", () => { if (state.ws) state.ws.close(1000, "Debugger page unloading"); stopMicrophone(); stopPlayback(); });
})();
