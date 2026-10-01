import { readFileSync } from 'node:fs';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { previewSocket, type WebSocketLike } from './previewSocket';
import { selectPreferredFrame } from '../viewport/lodPolicy';
import { LiveDimensions } from '../design/LiveDimensions';

class Socket implements WebSocketLike {
  binaryType=''; readyState=1; onopen=null; onerror=null;
  onmessage: WebSocketLike['onmessage']=null; onclose:WebSocketLike['onclose']=null;sent:string[]=[];
  send(s:string){this.sent.push(s)}
  close(){this.readyState=3;this.onclose?.({})}
  message(data:unknown){this.onmessage?.({data})}
}
function frame(overrides:Record<string,unknown>):ArrayBuffer {
  const original=readFileSync('../shared/frame-fixtures/good/minimal-preview.bin');
  const buffer=new ArrayBuffer(original.byteLength);new Uint8Array(buffer).set(original);
  const n=new DataView(buffer).getUint32(4,true);
  const header=JSON.parse(new TextDecoder().decode(new Uint8Array(buffer,8,n)));
  Object.assign(header,overrides);const bytes=new TextEncoder().encode(JSON.stringify(header));
  const payload=new Uint8Array(buffer,Math.ceil((8+n)/8)*8);
  const start=Math.ceil((8+bytes.length)/8)*8;const out=new ArrayBuffer(start+payload.length);
  new Uint8Array(out).set(original.subarray(0,4));new DataView(out).setUint32(4,bytes.length,true);
  new Uint8Array(out).set(bytes,8);new Uint8Array(out).set(payload,start);return out;
}
const OLD={dimensions_mm:{mouth_opening:[111,222],horn_overall:[123,234,345]},dimensions_status:'current'};
const NEW={dimensions_mm:{mouth_opening:[777,888],horn_overall:[789,890,901]},dimensions_status:'current'};
const PENDING={dimensions_mm:null,dimensions_status:'pending'};
describe('document-scoped design size regressions',()=>{
  let host:HTMLDivElement,root:Root,sockets:Socket[];
  const manager=previewSocket;
  function render(){act(()=>root.render(createElement('div',{className:'param-panel'},createElement('input',{'aria-invalid':false}),createElement(LiveDimensions))))}
  function msg(data:unknown,index=sockets.length-1){act(()=>sockets[index].message(data))}
  function request(){return JSON.parse(sockets.at(-1)!.sent.at(-1)!)}
  function answer(metadata:unknown,override:Record<string,unknown>={}){const r=request();msg(frame({epoch:r.epoch,seq:r.seq,designRevision:r.designRevision,lod:r.lod,previewMetadata:metadata,...override}))}
  beforeEach(()=>{
    vi.useFakeTimers();manager.stop();resetDesignStore();sockets=[];
    Object.assign(manager,{snapshot:{connection:'idle',epoch:null,frame:null,displayedRevision:null,lastValidRevision:null,stale:true,dropped:0,error:null,errorFields:null,errorRevision:null},barrierRevision:0,factory:()=>{const s=new Socket();sockets.push(s);return s}});
    (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
    host=document.createElement('div');document.body.append(host);root=createRoot(host);
    act(()=>manager.start());msg(JSON.stringify({v:1,kind:'hello',epoch:3,heartbeatSec:15}));render();answer(OLD);
  });
  afterEach(()=>{act(()=>root.unmount());manager.stop();host.remove();vi.useRealTimers()});
  it.each(['New','Open'])('%s while live updates are paused never displays the previous document numbers',(action)=>{
    act(()=>manager.stop());act(()=>{if(action==='New')resetDesignStore();else {const design=structuredClone(useDesignStore.getState().design);design.scale=2;useDesignStore.getState().loadDesign(design)}});
    expect(host.textContent).not.toContain('111.0 × 222.0');
  });
  it('New while paused, then reconnect, accepts the reset new document revision',()=>{
    act(()=>useDesignStore.setState({designRevision:57}));answer(OLD,{designRevision:57});
    act(()=>manager.stop());act(()=>resetDesignStore());act(()=>manager.start());
    msg(JSON.stringify({v:1,kind:'hello',epoch:4,heartbeatSec:15}));answer(NEW);
    expect(host.textContent).toContain('777.0 × 888.0');
  });
  it.each(['New','Open'])('repeated-epoch older reconnect clears readouts before paused %s',(action)=>{
    act(()=>resetDesignStore());answer(OLD,{lod:'fine'});
    for(let i=0;i<5;i++)act(()=>manager.refresh());
    answer(OLD,{lod:'fine'});
    expect(request().seq).toBe(7);
    act(()=>manager.stop());act(()=>manager.start());
    msg(JSON.stringify({v:1,kind:'hello',epoch:3,heartbeatSec:15}));
    answer(undefined);
    act(()=>vi.advanceTimersByTime(140));answer(undefined,{lod:'fine'});
    expect(manager.getSnapshot().frame?.header.previewMetadata?.dimensions_mm).toBeUndefined();
    act(()=>manager.stop());act(()=>{
      if(action==='New')resetDesignStore();
      else {const design=structuredClone(useDesignStore.getState().design);design.scale=2;useDesignStore.getState().loadDesign(design)}
    });
    expect(host.textContent).not.toContain('111.0 × 222.0');
    expect(host.querySelector('section')).toBeNull();
    expect(manager.getSnapshot().lastCanonicalDimensions).toBeUndefined();
    expect(manager.getSnapshot().dimensionsFrame?.header.previewMetadata?.dimensions_mm).toBeUndefined();
  });
  it.each(['New','Open'])('%s before repeated-epoch older reconnect keeps the card hidden',(action)=>{
    answer(OLD,{lod:'fine'});
    act(()=>manager.stop());act(()=>{
      if(action==='New')resetDesignStore();
      else {const design=structuredClone(useDesignStore.getState().design);design.scale=2;useDesignStore.getState().loadDesign(design)}
    });
    expect(host.querySelector('section')).toBeNull();
    act(()=>manager.start());msg(JSON.stringify({v:1,kind:'hello',epoch:3,heartbeatSec:15}));
    answer(undefined);act(()=>vi.advanceTimersByTime(140));answer(undefined,{lod:'fine'});
    expect(manager.getSnapshot().awaitingDocumentFrame).toBe(false);
    expect(host.querySelector('section')).toBeNull();
    expect(host.textContent).not.toContain('111.0');
  });
  it('repeated-epoch replacement owns the pending lane and cannot reuse cached numbers',()=>{
    for(let i=0;i<5;i++)act(()=>manager.refresh());answer(OLD,{lod:'fine'});
    const old=manager.getSnapshot().dimensionsFrame;
    act(()=>manager.stop());act(()=>manager.start());msg(JSON.stringify({v:1,kind:'hello',epoch:3,heartbeatSec:15}));
    answer(PENDING);
    expect(manager.getSnapshot().dimensionsFrame).not.toBe(old);
    expect(host.textContent).toContain('Updating dimensions');
    expect(host.textContent).not.toContain('111.0');
    act(()=>vi.advanceTimersByTime(140));answer(NEW);
    expect(host.textContent).toContain('777.0 × 888.0');
    expect(host.textContent).toContain('Current preview');
    msg(frame({epoch:3,seq:1,designRevision:1,lod:'coarse',previewMetadata:PENDING}));
    expect(host.textContent).toContain('Current preview');
    expect(host.textContent).toContain('777.0 × 888.0');
    // A callback from the replaced socket is ignored even when its epoch matches.
    msg(frame({epoch:3,seq:99,designRevision:1,lod:'fine',previewMetadata:OLD}),0);
    expect(host.textContent).not.toContain('111.0');
  });
  it('late coarse cannot turn an already settled current readout back into pending',()=>{
    const coarse=request();act(()=>vi.advanceTimersByTime(140));answer(NEW);
    expect(host.textContent).toContain('Current preview');
    msg(frame({epoch:3,seq:coarse.seq,designRevision:1,lod:'coarse',previewMetadata:PENDING}));
    expect(host.textContent).toContain('Current preview');
  });
  it('new-document error-only outcome releases the awaiting flag',()=>{
    act(()=>resetDesignStore());const r=request();
    msg(JSON.stringify({v:1,kind:'error',epoch:3,seq:r.seq,designRevision:1,code:'INVALID',message:'new failed'}));
    expect(manager.getSnapshot().awaitingDocumentFrame).toBe(false);
    expect(host.textContent).toContain('Dimensions unavailable');
    expect(host.textContent).not.toContain('111.0');
  });
  it('rapid New-New drops the first New frame and a previous document error',()=>{
    act(()=>resetDesignStore());const first=request();act(()=>resetDesignStore());const second=request();
    msg(frame({epoch:3,seq:first.seq,designRevision:1,previewMetadata:OLD}));expect(host.textContent).not.toContain('111.0');
    msg(JSON.stringify({v:1,kind:'error',epoch:3,seq:first.seq,designRevision:1,message:'late old error'}));expect(manager.getSnapshot().error).toBe(null);
    answer(NEW);expect(manager.getSnapshot().awaitingDocumentFrame).toBe(false);expect(host.textContent).toContain('777.0');
    expect(second.seq).toBeGreaterThan(first.seq);
  });
  it('New then reconnect/epoch change accepts a new document frame without dimension keys',()=>{
    act(()=>resetDesignStore());msg(frame({epoch:2,seq:2,designRevision:1,previewMetadata:OLD}));expect(manager.getSnapshot().awaitingDocumentFrame).toBe(true);
    act(()=>sockets[0].close());act(()=>vi.advanceTimersByTime(250));msg(JSON.stringify({v:1,kind:'hello',epoch:4,heartbeatSec:15}));answer({warnings:[]});
    expect(manager.getSnapshot().awaitingDocumentFrame).toBe(false);expect(host.textContent).not.toContain('111.0');expect(host.querySelector('section')).toBeNull();
  });
  it('New keeps exactly the previous viewport frame until the replacement arrives',()=>{
    const old=manager.getSnapshot().frame;act(()=>resetDesignStore());expect(manager.getSnapshot().frame).toBe(old);expect(host.querySelector('section')).toBeNull();
    answer(PENDING);expect(manager.getSnapshot().frame).not.toBe(old);expect(host.textContent).toContain('Updating dimensions');expect(host.textContent).not.toContain('111.0');
  });
  it('mounting card during document replacement still observes invalid drafts',async()=>{
    act(()=>root.render(null));act(()=>resetDesignStore());render();answer(NEW);
    await act(async()=>host.querySelector('input')!.setAttribute('aria-invalid','true'));
    expect(host.textContent).toContain('Last valid preview');
  });
  it('New after a high revision keeps card and actual preferred viewport frame on the same document',()=>{
    act(()=>useDesignStore.setState({designRevision:57}));answer(OLD,{designRevision:57,lod:'fine'});
    const displayed=manager.getSnapshot().frame;act(()=>resetDesignStore());answer(NEW,{lod:'fine'});
    const preferred=selectPreferredFrame(displayed,manager.getSnapshot().frame);
    expect(preferred).toBe(manager.getSnapshot().frame);
  });
  it('Open then undo does not resurrect prior document numbers',()=>{
    act(()=>{const design=structuredClone(useDesignStore.getState().design);design.scale=2;useDesignStore.getState().loadDesign(design)});
    const opened=request();act(()=>useDesignStore.getState().undo());
    msg(frame({epoch:3,seq:opened.seq,designRevision:opened.designRevision,previewMetadata:OLD}));
    expect(host.textContent).not.toContain('111.0');answer(NEW);expect(manager.getSnapshot().awaitingDocumentFrame).toBe(false);
  });
  it.each([
    ['width-implicit', 'requested auto × 240.0 mm, effective 350.0 × 348.6 mm'],
    ['height-implicit', 'requested 320.0 × auto mm, effective 348.6 × 350.0 mm'],
    ['both-explicit', 'requested 320.0 × 240.0 mm, effective 348.6 × 348.6 mm'],
    ['matching', '400.0 × 400.0 mm'],
    ['inactive', '348.6 × 348.6 mm'],
    ['both-implicit', '350.0 × 350.0 mm'],
  ])('formats the real emitted %s morph target', (name, expected) => {
    const fixtures = JSON.parse(new TextDecoder().decode(readFileSync('../shared/preview-fixtures/c2-dimensions-morph-metadata.json')));
    answer(fixtures[name].metadata, { lod: 'fine' });
    expect(host.querySelector('dd')?.textContent).toBe(expected);
    expect(host.textContent).toContain('Current preview');
  });
  it('absent dimension keys preserve existing same-revision error and badge behavior',()=>{
    answer(undefined);
    const r=request();
    msg(JSON.stringify({v:1,kind:'error',epoch:3,seq:r.seq,designRevision:1,message:'existing field error',fields:{'morph.corner_radius':'bad corner'}}));
    const before=manager.getSnapshot();
    let notifications=0;
    const unsubscribe=manager.subscribe(()=>notifications++);
    act(()=>resetDesignStore());
    const after=manager.getSnapshot();
    expect(after.frame).toBe(before.frame);
    expect(after.displayedRevision).toBe(before.displayedRevision);
    expect(after.lastValidRevision).toBe(before.lastValidRevision);
    expect(after.error).toBe(before.error);
    expect(after.errorFields).toBe(before.errorFields);
    expect(after.errorRevision).toBe(before.errorRevision);
    expect(after.stale).toBe(false);
    expect(notifications).toBe(1);
    unsubscribe();
    expect(host.querySelector('section')).toBeNull();
  });
});
