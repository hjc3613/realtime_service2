const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
  const nodes = new Map();
  const node = () => ({value:'',textContent:'',style:{},dataset:{},disabled:false,children:[],listeners:{},
    append(child){this.children.push(child);},
    prepend(child){this.children.unshift(child);},remove(){},pause(){},load(){},
    addEventListener(type,fn){this.listeners[type]=fn;},setAttribute(){},querySelector(){return this;},querySelectorAll(){return [];},
    classList:{add(){},remove(){},toggle(){}}});
  let tick;
  let now = 0;
  const sources = [];
  const sandbox = {document:{getElementById(id){
    if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);
  },querySelectorAll(){return [];},createElement:node},
    location:{protocol:'http:',host:'localhost'},window:{addEventListener(){}},
    WebSocket:{OPEN:1},setInterval(fn){tick=fn; return 1;},clearInterval(){},
    setTimeout(){},btoa(s){return Buffer.from(s,'binary').toString('base64');},
    atob(s){return Buffer.from(s,'base64').toString('binary');},performance:{now(){return now;}},
    Uint8Array,Float32Array,Math};
  const script = fs.readFileSync(path.join(__dirname,'../service_app/web/app.js'),'utf8');
  vm.runInNewContext(script.replace(/\}\)\(\);\s*$/, 'globalThis.audioTest = {state,captureAudio,startAudioClock,togglePause,receiveAudio,handleEvent};})();'), sandbox);
  const sent=[];
  const api=sandbox.audioTest;
  api.state.ws={readyState:1,bufferedAmount:0,send(data){sent.push(JSON.parse(data));}};
  api.state.audioContext={sampleRate:8000,currentTime:0,suspend(){},resume(){return Promise.resolve();},
    createBuffer(channels,length,rate){return {duration:length/rate,getChannelData(){return new Float32Array(length);}};},
    createBufferSource(){const source={connect(){},disconnect(){},start(){},stop(){this.stopped=true;}};sources.push(source);return source;}};
  api.state.microphoneOn=true;
  return {api,sent,tick:()=>tick(),nodes,sources,setTime(value){now=value;}};
}

function capture(api, packets) {
  api.captureAudio({inputBuffer:{getChannelData(){return new Float32Array(1600*packets).fill(0.1);}}});
}

test('capture backlog stays bounded and is drained on the next upload tick',()=>{
  const {api,sent,tick}=harness();
  api.startAudioClock(); sent.length=0;
  capture(api,10);
  assert.equal(api.state.capturePackets.length,2);
  assert.equal(api.state.droppedPackets,8);
  tick();
  assert.equal(sent.length,2);
  assert.equal(api.state.capturePackets.length,0);
});

test('socket backpressure cannot accumulate an unbounded audio upload queue',()=>{
  const {api,sent,tick}=harness();
  api.startAudioClock(); sent.length=0;
  api.state.ws.bufferedAmount=8192;
  for(let i=0;i<50;i++){capture(api,3); tick();}
  assert.equal(sent.length,0);
  assert.equal(api.state.capturePackets.length,2);
  assert.equal(api.state.droppedPackets,148);
  api.state.ws.bufferedAmount=0;
  tick();
  assert.equal(sent.length,2);
});

test('pause stops uploading immediately and discards queued microphone audio',async()=>{
  const {api,sent,tick}=harness();
  api.startAudioClock(); sent.length=0;
  capture(api,2);
  api.state.ws.send=data=>sent.push(JSON.parse(data));
  await api.togglePause();
  assert.equal(api.state.paused,true);
  assert.equal(api.state.capturePackets.length,0);
  assert.equal(sent[0].type,'debug.pause');
  tick(); capture(api,2);
  assert.equal(sent.length,1);
  assert.equal(api.state.capturePackets.length,0);
});

test('healthy capture does not gain an extra silent packet on an empty upload tick',()=>{
  const {api,sent,tick,setTime}=harness();
  api.state.lastCaptureAt=0;
  api.startAudioClock();
  assert.equal(sent.length,0);
  setTime(200); capture(api,1); tick();
  assert.equal(sent.length,1);
  setTime(220); tick();
  assert.equal(sent.length,1);
  setTime(600); tick();
  assert.equal(sent.length,2); // Stalled capture still advances TTS with silence.
});

test('microphone off keeps the TTS clock running',()=>{
  const {api,sent,tick}=harness();
  api.state.microphoneOn=false;
  api.startAudioClock(); tick();
  assert.equal(sent.length,2);
  assert.ok(sent.every(e=>Buffer.from(e.audio,'base64').every(byte=>byte===255)));
});

test('confirmed speech stops all scheduled audio and resets the playback cursor',async()=>{
  const {api,sources}=harness();
  const audio=Buffer.alloc(1600,255).toString('base64');
  await api.receiveAudio(audio); await api.receiveAudio(audio);
  assert.equal(api.state.playbackSources.size,2);
  api.handleEvent({type:'input_audio_buffer.speech_started'});
  assert.ok(sources.every(source=>source.stopped));
  assert.equal(api.state.playbackSources.size,0);
  assert.equal(api.state.nextPlaybackAt,0);
  await api.receiveAudio(audio);
  assert.equal(api.state.playbackSources.size,1);
  assert.ok(api.state.nextPlaybackAt<0.3);
});

test('interruption invalidates audio awaiting an AudioContext resume',async()=>{
  const {api,sources}=harness();
  let release;
  api.state.audioContext.resume=()=>new Promise(resolve=>{release=resolve;});
  const receive=api.receiveAudio(Buffer.alloc(1600,255).toString('base64'));
  api.handleEvent({type:'response.cancelled'});
  release(); await receive;
  assert.equal(sources.length,0);
});

test('classifier audio is loaded and made exportable only after inspection',()=>{
  const {api,sent}=harness();
  api.handleEvent({type:'debug.classifier.started',request_id:'cls_1',kind:'interrupt',audio_clock_ms:0,audio_ms:3000,choices:['interrupt','ignorable'],messages:null});
  const record=api.state.classifiers.get('cls_1');
  assert.equal(record.download.disabled,true);
  record.inputArea.children[0].listeners.click();
  assert.equal(sent[0].type,'debug.classifier.inspect');
  api.handleEvent({type:'debug.classifier.input',request_id:'cls_1',messages:[{role:'user',content:[]}],error:null});
  assert.equal(record.download.disabled,false);
  assert.equal(record.messages[0].role,'user');
});
