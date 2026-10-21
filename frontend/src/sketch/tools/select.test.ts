// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { hitDimension } from './dimension';
import { importedSketch } from '../ops';
import { combineSelection, cycleHit, dragPointIds, finishSelect, marqueeSelection, moveSelect, selectHits, startSelect } from './select';
const sketch = () => importedSketch([
  { id: 'a', x: 1, y: 1 }, { id: 'b', x: 3, y: 1 },
  { id: 'c', x: -2, y: 2 }, { id: 'd', x: 6, y: 2 },
], [{ id: 'inside', kind: 'line', p1: 'a', p2: 'b' }, { id: 'through', kind: 'line', p1: 'c', p2: 'd' }]);
describe('Select state machine', () => {
  it('separates clicks, window/crossing marquees, and body drags at four pixels', () => {
    const empty = startSelect({x:0,y:0}, undefined, [], {}), body = startSelect({x:0,y:0}, 'inside', ['inside'], {});
    expect(moveSelect(empty,{x:3,y:0}).phase).toBe('pending');
    expect(moveSelect(empty,{x:4,y:0}).phase).toBe('marquee');
    expect(moveSelect(body,{x:4,y:0}).phase).toBe('drag');
    expect(moveSelect(moveSelect(body,{x:4,y:0}),{x:0,y:0}).phase).toBe('drag');
  });
  it('keeps a group for a body drag and resolves a plain click to the hit member', () => {
    const state = startSelect({x:0,y:0}, 'inside', ['inside','through'], {});
    expect(finishSelect(state, sketch(), state.start, state.current)).toEqual(['inside']);
    const dragged = moveSelect(state, {x:10,y:0});
    expect(finishSelect(dragged, sketch(), state.start, dragged.current)).toEqual(['inside','through']);
    expect(finishSelect({...state, modifiers:{shift:true}}, sketch(), state.start, state.current)).toEqual(['inside','through']);
    expect(finishSelect({...state, hit:undefined}, sketch(), state.start, state.current)).toEqual([]);
    const marquee = moveSelect(startSelect({x:0,y:0}, undefined, ['old'], {shift:true}), {x:4,y:4});
    expect(finishSelect(marquee, sketch(), marquee.start, marquee.current)).toEqual(['old','a','b','inside']);
  });
  it('windows require entire items; crossing selects touched curves, excluding dimensions and context', () => {
    const s = sketch();
    expect(marqueeSelection(s,{x:0,y:0},{x:4,y:4})).toEqual(['a','b','inside']);
    expect(marqueeSelection(s,{x:4,y:4},{x:0,y:0})).toEqual(['a','b','inside','through']);
    s.entities[1].reference = true;
    s.constraints.push({ id:'dimension',kind:'distance',a:'a',b:'b',value:2,driving:true });
    expect(marqueeSelection(s,{x:4,y:4},{x:0,y:0})).toEqual(['a','b','inside']);
  });
  it('uses arc extrema and finite circular intersections instead of chord approximations', () => {
    const s = importedSketch([{id:'centre',x:0,y:0},{id:'start',x:-2,y:0},{id:'end',x:2,y:0}], [{id:'arc',kind:'arc',center:'centre',start:'start',end:'end',ccw:false}]);
    expect(marqueeSelection(s,{x:-3,y:-.5},{x:3,y:.5})).not.toContain('arc');
    expect(marqueeSelection(s,{x:.5,y:1.9},{x:-.5,y:2.1})).toContain('arc');
    expect(marqueeSelection(s,{x:-3,y:-.5},{x:3,y:3})).toContain('arc');
  });
  it('Shift adds, Ctrl/Cmd toggles and plain selection replaces', () => {
    expect(combineSelection(['old'],['a','b'],{shift:true})).toEqual(['old','a','b']);
    expect(combineSelection(['a','old'],['a','b'],{toggle:true})).toEqual(['old','b']);
    expect(combineSelection(['old'],['a'])).toEqual(['a']);
  });
  it('cycles points, curves, dimensions and resets outside 3 pixels or 600 ms', () => {
    const ids = selectHits(sketch(),{x:1,y:1},.01,[],['dimension']);
    expect(ids).toEqual(['a','inside','dimension']);
    const first = cycleHit(undefined,{x:20,y:20},100,ids);
    const second = cycleHit(first,{x:22,y:20},700,ids);
    expect(second.index).toBe(1);
    expect(cycleHit(second,{x:22,y:20},800,ids).index).toBe(2);
    expect(cycleHit(second,{x:26,y:20},800,ids).index).toBe(0);
    expect(cycleHit(second,{x:22,y:20},1301,ids).index).toBe(0);
  });
  it('collects unique line ends, arc centres/ends, and explicit selected points', () => {
    const s = sketch(); s.entities.push({id:'arc',kind:'arc',center:'c',start:'b',end:'d',ccw:true});
    expect(dragPointIds(s,['inside','arc','a'])).toEqual(['a','b','c','d']);
  });
  it.each(['dimensionHelper', 'angleBaseline'])('excludes %s geometry and points from marquee, cycling and body drag', marker => {
    const s = sketch();
    s.points.push({id:'helper.a',x:1,y:1}, {id:'helper.b',x:3,y:1});
    s.entities.push({id:'helper',kind:'line',p1:'helper.a',p2:'helper.b',construction:true,data:{[marker]:true}});
    expect(marqueeSelection(s,{x:0,y:0},{x:4,y:4})).toEqual(['a','b','inside']);
    expect(marqueeSelection(s,{x:4,y:4},{x:0,y:0})).toEqual(['a','b','inside','through']);
    expect(selectHits(s,{x:1,y:1},.01)).toEqual(['a','inside']);
    expect(dragPointIds(s,['inside','helper','helper.a'])).toEqual(['a','b']);
  });
});


it('shares point priority, nearest curves, and hidden centres with dimension and marquee', () => {
  const s = importedSketch([{id:'center',x:0,y:0},{id:'start',x:-2,y:0},{id:'end',x:2,y:0}],
    [{id:'arc',kind:'arc',center:'center',start:'start',end:'end',ccw:false}]);
  const hidden = ['center'];
  for (const at of [{x:0,y:0},{x:1.99,y:.01},{x:0,y:2}]) {
    expect(selectHits(s,at,.1,hidden)[0]).toBe(hitDimension(s,[],at,.1,hidden));
  }
  expect(marqueeSelection(s,{x:-3,y:-3},{x:3,y:3},hidden)).toEqual(['start','end','arc']);
  const gesture = moveSelect(startSelect({x:-3,y:-3},undefined,[],{}),{x:3,y:3});
  expect(finishSelect(gesture,s,gesture.start,gesture.current,hidden)).not.toContain('center');
});
