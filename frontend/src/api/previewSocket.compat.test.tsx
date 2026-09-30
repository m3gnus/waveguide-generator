import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { PreviewSocketManager, type WebSocketLike } from './previewSocket';


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

// Shared fields and notification counts captured from base 44226173 with no dimension keys.
const baseCheckpoints: Record<string, unknown> = {
  "paused-edit": {
    "label": "paused-edit",
    "notifications": 0,
    "displayedRevision": 1,
    "lastValidRevision": 1,
    "error": null,
    "errorRevision": null,
    "errorFields": null,
    "stale": false,
    "frameRevision": 1
  },
  "paused-new": {
    "label": "paused-new",
    "notifications": 0,
    "displayedRevision": 1,
    "lastValidRevision": 1,
    "error": null,
    "errorRevision": null,
    "errorFields": null,
    "stale": false,
    "frameRevision": 1
  },
  "same-revision-new": {
    "label": "same-revision-new",
    "notifications": 1,
    "displayedRevision": 1,
    "lastValidRevision": 1,
    "error": "existing",
    "errorRevision": 1,
    "errorFields": {
      "profile.length": "existing"
    },
    "stale": false,
    "frameRevision": 1
  },
  "high-new": {
    "label": "high-new",
    "notifications": 1,
    "displayedRevision": null,
    "lastValidRevision": 57,
    "error": "old-error",
    "errorRevision": 57,
    "errorFields": {
      "profile.length": "old-error"
    },
    "stale": true,
    "frameRevision": 57
  },
  "high-new-error": {
    "label": "high-new-error",
    "notifications": 0,
    "displayedRevision": null,
    "lastValidRevision": 57,
    "error": "old-error",
    "errorRevision": 57,
    "errorFields": {
      "profile.length": "old-error"
    },
    "stale": true,
    "frameRevision": 57
  },
  "late-previous-error": {
    "label": "late-previous-error",
    "notifications": 1,
    "displayedRevision": 1,
    "lastValidRevision": 1,
    "error": "late-error",
    "errorRevision": 1,
    "errorFields": null,
    "stale": true,
    "frameRevision": 1
  }
};

describe('older-mesher shared-state compatibility',()=>{
let m:PreviewSocketManager,s:Socket,notifications:number,unsubscribe:()=>void;
function req(){return JSON.parse(s.sent.at(-1)!)}
function answer(rev=useDesignStore.getState().designRevision){const r=req();s.message(frame({epoch:r.epoch,seq:r.seq,designRevision:rev,lod:'fine',previewMetadata:undefined}))}
function err(rev:number,message:string){const r=req();s.message(JSON.stringify({v:1,kind:'error',epoch:r.epoch,seq:r.seq,designRevision:rev,message,fields:{'profile.length':message}}))}
function trace(label:string){
  const a=m.getSnapshot();
  expect({label,notifications,displayedRevision:a.displayedRevision,lastValidRevision:a.lastValidRevision,
    error:a.error,errorRevision:a.errorRevision,errorFields:a.errorFields,stale:a.stale,
    frameRevision:a.frame?.header.designRevision}).toEqual(baseCheckpoints[label]);
  notifications=0;
}
beforeEach(()=>{vi.useFakeTimers();resetDesignStore();s=new Socket();m=new PreviewSocketManager(()=>s);m.start();s.message(JSON.stringify({v:1,kind:'hello',epoch:3,heartbeatSec:15}));notifications=0;unsubscribe=m.subscribe(()=>notifications++)});
afterEach(()=>{unsubscribe();m.stop();vi.useRealTimers()});
it('paused edit and New',()=>{answer();m.stop();notifications=0;useDesignStore.getState().updateValue('scale',2);trace('paused-edit');resetDesignStore();trace('paused-new')});
it('same revision New original probe',()=>{answer();err(1,'existing');notifications=0;resetDesignStore();trace('same-revision-new')});
it('New from high revision, error only',()=>{useDesignStore.setState({designRevision:57});answer(57);err(57,'old-error');notifications=0;resetDesignStore();trace('high-new');err(1,'new-error');trace('high-new-error')});
it('late previous-document error after same-revision New',()=>{answer();const previous=req();resetDesignStore();notifications=0;s.message(JSON.stringify({v:1,kind:'error',epoch:3,seq:previous.seq,designRevision:1,message:'late-error'}));trace('late-previous-error')});
});
