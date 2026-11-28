"""실제 펜 JS와 포인터 이벤트, 뷰어 도구 연결을 오프라인 검증한다."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_js(tmp_path, source):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js가 없는 환경")
    program = tmp_path / "pen_test.cjs"
    program.write_text(
        "const assert=require('node:assert/strict');\nconst {InkLayer}=require("
        + json.dumps(str(ROOT / "ui/dicom_canvas/ink.js")) + ");\n" + source,
        encoding="utf-8",
    )
    subprocess.run([node, str(program)], capture_output=True, text=True, encoding="utf-8", check=True)


def test_ink_uses_image_coordinates_and_projects_with_zoom(tmp_path):
    run_js(tmp_path, r'''
const layer=new InkLayer(); layer.setContext('case-a');
layer.begin([10,20],'#ff4d4f',4,2); layer.move([20,30]); layer.finish([30,40]);
assert.deepEqual(layer.strokes[0].points,[[10,20],[20,30],[30,40]]);
assert.equal(layer.strokes[0].width,2);
const moves=[], widths=[];
const ctx={save(){},restore(){},beginPath(){},lineTo(){},stroke(){widths.push(this.lineWidth)},
    moveTo(x,y){moves.push([x,y])}};
layer.draw(ctx,{x:0,y:110},[0,0,200,100],2);
layer.draw(ctx,{x:0,y:110},[10,20,100,50],4);
assert.deepEqual(moves,[[21,151],[2,112]]);
assert.deepEqual(widths,[4,8]);
assert.deepEqual(layer.strokes[0].points,[[10,20],[20,30],[30,40]]);
''')


def test_eraser_sweep_only_removes_touched_strokes_and_can_undo(tmp_path):
    run_js(tmp_path, r'''
const layer=new InkLayer(); layer.setContext('a');
for(const y of [0,30]){layer.begin([0,y],'#ffe34d',2,1);layer.finish([100,y])}
layer.beginErase(); layer.erase([50,-10],[50,10],1); layer.finishErase();
assert.equal(layer.strokes.length,1);
assert.deepEqual(layer.strokes[0].points,[[0,30],[100,30]]);
layer.undo(); assert.equal(layer.strokes.length,2);
layer.undo(); assert.equal(layer.strokes.length,1);
layer.clear(); assert.equal(layer.strokes.length,0);
layer.undo(); assert.equal(layer.strokes.length,1);
''')


def test_cancelled_pointer_does_not_commit_ink_or_erase(tmp_path):
    run_js(tmp_path, r'''
const layer=new InkLayer(); layer.setContext('a');
layer.begin([10,10],'#30e878',8,1);layer.finish([10,10]);
assert.equal(layer.strokes[0].points.length,1);
layer.begin([50,50],'#ff4d4f',4,1);layer.move([60,60]);layer.cancel();
assert.equal(layer.strokes.length,1);
layer.beginErase();layer.erase([10,10],[10,10],1);
assert.equal(layer.strokes.length,0);layer.cancel();assert.equal(layer.strokes.length,1);
layer.beginErase();layer.erase([80,80],[90,90],1);layer.finishErase();
assert.equal(layer.history.length,1); // 닿지 않은 지우기는 이력을 만들지 않음
''')


def test_commands_are_not_replayed_and_context_change_clears_ink(tmp_path):
    run_js(tmp_path, r'''
const layer=new InkLayer();layer.setContext('a');
for(const x of [0,20]){layer.begin([x,0],'#30e878',4,1);layer.finish([x,10])}
layer.applyCommand({id:1,action:'undo'});assert.equal(layer.strokes.length,1);
layer.applyCommand({id:1,action:'undo'});assert.equal(layer.strokes.length,1);
layer.setContext('a');assert.equal(layer.strokes.length,1);
layer.setContext('another-user:another-case');
assert.equal(layer.strokes.length,0);assert.equal(layer.history.length,0);
''')


def browser_harness():
    source = (ROOT / "ui/dicom_canvas/index.html").read_text(encoding="utf-8").split("<script>")[1].split("</script>")[0]
    return "const SOURCE=" + json.dumps(source) + ";\n" + r'''
const vm=require('node:vm');
const handlers={},windows={},messages=[];
const ctx=new Proxy({}, {get:(o,k)=> k in o ? o[k] :
    k==='measureText' ? s=>({width:s.length*6}) : ()=>{}});
const canvas={parentElement:{clientWidth:400},style:{},getContext:()=>ctx,
    getBoundingClientRect:()=>({left:0,top:0}),focus(){},setPointerCapture(){},releasePointerCapture(){},
    addEventListener:(name,fn)=>handlers[name]=fn};
const parent={postMessage:data=>messages.push(data)};
const sandbox={InkLayer,document:{querySelector:()=>canvas},devicePixelRatio:1,
    window:{parent,addEventListener:(name,fn)=>windows[name]=fn},
    Image:class{set src(value){this.onload()}}};
vm.createContext(sandbox);vm.runInContext(SOURCE,sandbox);
function render(mode='pen',token='user:case-a',command=null,extra={}){
    windows.message({source:parent,data:{type:'streamlit:render',args:{
        image:'fixture',token,drawing_token:token,epoch:0,max_height:420,crop:[0,0,200,100],
        cols:200,rows:100,zoom:1,points:[],boxes:[],mode,pen_color:'#ff4d4f',pen_width:4,
        pen_command:command,ink_enabled:true,...extra}}});
}
function pointer(x,y,pointerType='mouse',pointerId=1){
    return {clientX:x,clientY:y,pointerType,pointerId,button:0,isPrimary:true,preventDefault(){}};
}
const state=()=>vm.runInContext('ink',sandbox);
'''


@pytest.mark.parametrize("pointer_type", ["mouse", "touch", "pen"])
def test_real_pointer_handlers_draw_without_sending_ink_to_server(tmp_path, pointer_type):
    run_js(tmp_path, browser_harness() + r'''
render();
handlers.pointerdown(pointer(21,151,TYPE));
handlers.pointermove(pointer(41,171,TYPE));
handlers.pointerup(pointer(61,191,TYPE));
assert.deepEqual(state().strokes[0].points,[[10,20],[20,30],[30,40]]);
assert.equal(messages.filter(m=>m.type==='streamlit:setComponentValue').length,0);
// WL/theme rerender와 도구 전환은 그림을 보존한다.
render('navigate');assert.equal(state().strokes.length,1);
render('eraser');handlers.pointerdown(pointer(41,171,TYPE));handlers.pointerup(pointer(41,171,TYPE));
assert.equal(state().strokes.length,0);
render('eraser','user:case-a',{id:1,action:'undo'});assert.equal(state().strokes.length,1);
render('navigate','user:case-a',{id:1,action:'undo'});assert.equal(state().strokes.length,1);
render('pen','user:case-b');assert.equal(state().strokes.length,0);
'''.replace("TYPE", json.dumps(pointer_type)))


def test_readonly_secondary_pointer_and_cancel_cannot_change_completed_strokes(tmp_path):
    run_js(tmp_path, browser_harness() + r'''
render('pen','a',null,{read_only:true});
handlers.pointerdown(pointer(21,151));handlers.pointerup(pointer(41,171));
assert.equal(state().strokes.length,0);
render('pen','a');handlers.pointerdown(pointer(21,151,'touch',1));
handlers.pointerdown({...pointer(61,191,'touch',2),isPrimary:false});
handlers.pointermove(pointer(81,211,'touch',2));
handlers.pointerup(pointer(81,211,'touch',2));
assert.equal(state().strokes.length,0);
handlers.pointercancel(pointer(21,151,'touch',1));assert.equal(state().strokes.length,0);
render('pen','a',null,{zoom:2});handlers.contextmenu(pointer(41,171));
assert.equal(messages.filter(m=>m.type==='streamlit:setComponentValue').length,0);
render('measure','a');handlers.pointerdown(pointer(21,151));handlers.pointerup(pointer(21,151));
const events=messages.filter(m=>m.type==='streamlit:setComponentValue');
assert.equal(events.length,1);assert.equal(events[0].value.kind,'measure');
''')


def test_reader_pen_controls_and_commands_preserve_measurements(viewer):
    viewer.run()
    from test_viewer import canvas_event
    canvas_event(viewer, "measure", point=[1, 2])
    canvas_event(viewer, "measure", point=[4, 6])
    viewer.radio(key="viewer_tool").set_value("펜").run()
    assert not viewer.exception
    assert viewer.selectbox(key="viewer_pen_color").options == ["빨강", "노랑", "초록"]
    viewer.selectbox(key="viewer_pen_color").select("초록").run()
    viewer.button(key="pen_undo").click().run()
    assert viewer.session_state["viewer_pen_command"]["action"] == "undo"
    sequence = viewer.session_state["viewer_pen_command"]["id"]
    viewer.button(key="pen_redo").click().run()
    assert viewer.session_state["viewer_pen_command"] == {"id": sequence + 1, "action": "redo"}
    viewer.button(key="pen_clear").click().run()
    assert viewer.session_state["viewer_pen_command"] == {"id": sequence + 2, "action": "clear"}
    viewer.radio(key="viewer_tool").set_value("펜 선택").run()
    assert not viewer.exception
    assert viewer.session_state["viewer_points"] == [[1, 2], [4, 6]]
    assert viewer.metric[0].value == "5.00 px"


def test_other_roles_do_not_get_pen_controls(viewer):
    viewer.session_state["user"] = {"uid": "manager", "role": "manager", "email": "manager@test.com"}
    viewer.run()
    assert not viewer.exception
    assert "펜" not in viewer.radio(key="viewer_tool").options
    assert "펜 선택" not in viewer.radio(key="viewer_tool").options
    assert not any(b.key == "pen_clear" for b in viewer.button)


def test_redo_restores_edits_and_new_edits_discard_redo(tmp_path):
    run_js(tmp_path, r'''
const layer=new InkLayer();layer.setContext('a');
layer.begin([0,0],'#ff4d4f',4,1);layer.finish([20,20]);
const original=JSON.stringify(layer.strokes);
layer.undo();assert.equal(layer.strokes.length,0);
layer.redo();assert.equal(JSON.stringify(layer.strokes),original);
layer.beginErase();layer.erase([10,10],[10,10],1);layer.finishErase();
layer.undo();assert.equal(layer.strokes.length,1);layer.redo();assert.equal(layer.strokes.length,0);
layer.undo();layer.clear();layer.undo();layer.redo();assert.equal(layer.strokes.length,0);
layer.undo();layer.undo(); // 그림을 그리기 전으로 돌아가 새 그림 시작
layer.begin([50,50],'#ffe34d',2,1);layer.finish([60,60]);
layer.redo();assert.equal(layer.strokes.length,1);
assert.deepEqual(layer.strokes[0].points,[[50,50],[60,60]]);
''')


def test_copy_paste_clones_only_selected_ink_and_is_undoable(tmp_path):
    run_js(tmp_path, r'''
const layer=new InkLayer();layer.setContext('a');
for(const x of [10,60]){layer.begin([x,10],'#30e878',4,2);layer.finish([x+10,20])}
const originals=JSON.stringify(layer.strokes);
layer.copy();layer.paste(200,100,5);assert.equal(layer.strokes.length,2); // 선택하지 않으면 복사하지 않음
layer.selectAt([15,15],1);assert.equal(layer.selected.size,1);
layer.copy();layer.paste(200,100,5);
assert.equal(layer.strokes.length,3);assert.equal(layer.selected.size,1);
assert.deepEqual(layer.strokes[2].points,[[15,15],[25,25]]);
assert.equal(layer.strokes[2].width,2);assert.equal(layer.strokes[2].color,'#30e878');
assert.equal(JSON.stringify(layer.strokes.slice(0,2)),originals);
assert.notEqual(layer.strokes[2].points,layer.clipboard[0].points);
layer.undo();assert.equal(layer.strokes.length,2);layer.redo();assert.equal(layer.strokes.length,3);
layer.paste(200,100,5);assert.deepEqual(layer.strokes[3].points,[[20,20],[30,30]]);
layer.undo();layer.selectAll();layer.deleteSelected();assert.equal(layer.strokes.length,0);
layer.undo();assert.equal(layer.strokes.length,3);layer.redo();assert.equal(layer.strokes.length,0);
''')


def test_selection_moves_as_group_clamps_and_cancel_restores(tmp_path):
    run_js(tmp_path, r'''
const layer=new InkLayer();layer.setContext('a');
for(const x of [10,60]){layer.begin([x,10],'#30e878',4,1);layer.finish([x+10,20])}
const original=JSON.stringify(layer.strokes);
layer.selectAll();layer.beginTranslate();layer.translate(1000,-1000,100,100);
assert.deepEqual(layer.strokes.map(s=>s.points),[[[39,0],[49,10]],[[89,0],[99,10]]]);
layer.cancel();assert.equal(JSON.stringify(layer.strokes),original);assert.equal(layer.selected.size,2);
layer.beginTranslate();layer.translate(10,5,100,100);layer.finishTranslate();
assert.deepEqual(layer.strokes[0].points,[[20,15],[30,25]]);
layer.undo();assert.equal(JSON.stringify(layer.strokes),original);
layer.redo();assert.deepEqual(layer.strokes[1].points,[[70,15],[80,25]]);
layer.selectAt([25,20],1);layer.selectAt([75,20],1,true);assert.equal(layer.selected.size,2);
layer.selectAt([25,20],1,true);assert.equal(layer.selected.size,1);
layer.selectAt([0,90],1);assert.equal(layer.selected.size,0);
''')


def test_paste_stays_inside_image_and_context_clears_clipboard_selection_and_redo(tmp_path):
    run_js(tmp_path, r'''
const layer=new InkLayer();layer.setContext('a');
layer.begin([80,80],'#ffe34d',8,1);layer.finish([99,99]);
layer.selectAll();layer.copy();layer.paste(100,100,100);
assert.deepEqual(layer.strokes[1].points,[[80,80],[99,99]]);
layer.undo();assert.equal(layer.future.length,1);
layer.selectAll();layer.setContext('a');assert.equal(layer.selected.size,1);
layer.setContext('other-user:other-image');
assert.equal(layer.clipboard.length,0);assert.equal(layer.selected.size,0);
assert.equal(layer.history.length,0);assert.equal(layer.future.length,0);
layer.paste(100,100,10);layer.redo();assert.equal(layer.strokes.length,0);
''')


def keyboard_harness():
    return browser_harness() + r'''
function key(value,extra={}){
    const event={key:value,ctrlKey:true,target:canvas,prevented:false,
        preventDefault(){this.prevented=true},...extra};
    handlers.keydown(event);return event;
}
function stroke(){handlers.pointerdown(pointer(21,151));handlers.pointerup(pointer(61,191))}
'''


def test_keyboard_shortcuts_copy_move_delete_undo_and_redo_without_server_writes(tmp_path):
    run_js(tmp_path, keyboard_harness() + r'''
render();stroke();
assert.equal(key('z').prevented,true);assert.equal(state().strokes.length,0);
key('y');assert.equal(state().strokes.length,1);
key('z');key('Z',{shiftKey:true});assert.equal(state().strokes.length,1);
key('a');assert.equal(state().selected.size,1);
key('c');key('v');assert.equal(state().strokes.length,2);
assert.deepEqual(state().strokes[1].points,[[15,25],[35,45]]);
// Ctrl+A 선택 후 펜 모드에서도 선택 그림을 이동한다. 새 선이 생기면 안 된다.
key('a');handlers.pointerdown(pointer(21,151));
handlers.pointermove(pointer(41,171));handlers.pointerup(pointer(41,171));
assert.equal(state().strokes.length,2);
assert.deepEqual(state().strokes[0].points,[[20,30],[40,50]]);
key('z');assert.deepEqual(state().strokes[0].points,[[10,20],[30,40]]);
key('a');key('Delete',{ctrlKey:false});assert.equal(state().strokes.length,0);
key('z');key('a');key('Escape',{ctrlKey:false});assert.equal(state().selected.size,0);
assert.equal(messages.filter(m=>m.type==='streamlit:setComponentValue').length,0);
''')


@pytest.mark.parametrize("blocked", ["input", "readonly", "disabled", "not_reader", "composing", "alt"])
def test_shortcuts_do_not_take_over_text_inputs_or_readonly_canvas(tmp_path, blocked):
    run_js(tmp_path, keyboard_harness() + r'''
render();stroke();
let extra={};
if(BLOCK==='input')extra.target={tagName:'INPUT'};
if(BLOCK==='readonly')render('pen','user:case-a',null,{read_only:true});
if(BLOCK==='disabled')vm.runInContext('args.disabled=true',sandbox);
if(BLOCK==='not_reader')render('pen','user:case-a',null,{ink_enabled:false});
if(BLOCK==='composing')extra.isComposing=true;
if(BLOCK==='alt')extra.altKey=true;
for(const command of ['z','y','a','c','v'])assert.equal(key(command,extra).prevented,false);
assert.equal(state().strokes.length,1);assert.equal(state().selected.size,0);
'''.replace("BLOCK", json.dumps(blocked)))


def test_mac_shortcuts_and_korean_keyboard_code_and_repeat_guard(tmp_path):
    run_js(tmp_path, keyboard_harness() + r'''
render();stroke();
key('z',{ctrlKey:false,metaKey:true});assert.equal(state().strokes.length,0);
key('Z',{ctrlKey:false,metaKey:true,shiftKey:true});assert.equal(state().strokes.length,1);
key('ㅁ',{code:'KeyA'});assert.equal(state().selected.size,1);
key('c');key('v',{repeat:true});assert.equal(state().strokes.length,1);
key('v');assert.equal(state().strokes.length,2);
''')


def test_selection_tool_drag_and_escape_restore_single_history_entry(tmp_path):
    run_js(tmp_path, keyboard_harness() + r'''
render();stroke();render('ink_select');
const original=JSON.stringify(state().strokes);
handlers.pointerdown(pointer(41,171));handlers.pointermove(pointer(61,191));
assert.notEqual(JSON.stringify(state().strokes),original);
key('Escape',{ctrlKey:false});assert.equal(JSON.stringify(state().strokes),original);
handlers.pointerup(pointer(61,191));assert.equal(JSON.stringify(state().strokes),original);
handlers.pointerdown(pointer(41,171));handlers.pointermove(pointer(61,191));
handlers.pointermove(pointer(81,211));handlers.pointerup(pointer(81,211));
assert.equal(state().history.length,2); // 선 추가와 이동을 각각 한 작업으로 기록
assert.deepEqual(state().strokes[0].points,[[30,40],[50,60]]);
key('z');assert.equal(JSON.stringify(state().strokes),original);
render('ink_select','user:case-a',null,{zoom:2,crop:[10,20,100,50]});
key('a');key('c');key('v');
assert.deepEqual(state().strokes[1].points,[[12.5,22.5],[32.5,42.5]]);
''')


def test_rectangle_selection_hits_crossing_segments_dots_and_stroke_edges(tmp_path):
    run_js(tmp_path, r'''
const layer=new InkLayer();layer.setContext('a');
for(const [a,b] of [[[0,50],[100,50]],[[45,45],[45,45]],[[62,50],[62,50]],[[0,30],[30,0]]]){
    layer.begin(a,'#ff4d4f',4,1);layer.finish(b);
}
const before=JSON.stringify(layer.strokes), history=layer.history.length;
layer.selectRect([40,40],[60,60]);
assert.deepEqual([...layer.selected],layer.strokes.slice(0,3));
layer.selectRect([60,60],[40,40]);assert.equal(layer.selected.size,3);
// 경계 상자만 겹치고 실제 선은 닿지 않는 경우를 제외한다.
layer.selectRect([24,24],[26,26]);assert.equal(layer.selected.size,0);
layer.selectAt([0,50],1);layer.selectRect([44,44],[46,46],true);
assert.deepEqual([...layer.selected],layer.strokes.slice(0,2));
assert.equal(JSON.stringify(layer.strokes),before);assert.equal(layer.history.length,history);
''')


@pytest.mark.parametrize("mode", ["pen", "ink_select"])
@pytest.mark.parametrize("reverse", [False, True])
def test_right_drag_previews_rectangle_selects_and_copies_ink(tmp_path, mode, reverse):
    run_js(tmp_path, keyboard_harness() + r'''
render();stroke();
handlers.pointerdown(pointer(201,271));handlers.pointerup(pointer(241,291));
const original=JSON.stringify(state().strokes);
render(MODE);const rectangles=[];ctx.strokeRect=(...r)=>rectangles.push(r);
const a=REVERSE ? [71,201] : [11,141], b=REVERSE ? [11,141] : [71,201];
const right=(p,extra={})=>({...pointer(...p),button:2,...extra});
handlers.pointerdown(right(a));handlers.contextmenu(right(a));
handlers.pointermove(right(b));
assert.deepEqual(rectangles.at(-1),[11,141,60,60]); // 화면에 선택 사각형을 표시
assert.equal(state().selected.size,0); // 버튼을 놓기 전에는 확정하지 않음
handlers.pointerup(right(b));
assert.equal(state().selected.size,1);assert.equal(state().selected.has(state().strokes[0]),true);
assert.equal(JSON.stringify(state().strokes),original);assert.equal(state().history.length,2);
key('c');key('v');assert.equal(state().strokes.length,3);
assert.deepEqual(state().strokes[2].points,[[15,25],[35,45]]);
handlers.pointerdown(right([191,261],{shiftKey:true}));
handlers.pointermove(right([251,301]));handlers.pointerup(right([251,301]));
assert.equal(state().selected.size,2); // 붙여넣은 선 선택에 두 번째 선을 추가
assert.equal(messages.filter(m=>m.type==='streamlit:setComponentValue').length,0);
'''.replace("MODE", json.dumps(mode)).replace("REVERSE", json.dumps(reverse)))


def test_right_selection_cancel_click_and_pointer_ownership_do_not_edit_ink(tmp_path):
    run_js(tmp_path, keyboard_harness() + r'''
render();stroke();key('a');const before=JSON.stringify(state().strokes);
const right=(x,y,extra={})=>({...pointer(x,y),button:2,...extra});
handlers.pointerdown(right(11,141));handlers.pointerup(right(11,141));
assert.equal(state().selected.size,1); // 우클릭만 하면 기존 선택 유지
handlers.pointerdown(right(11,141));handlers.pointermove(right(71,201));
handlers.pointercancel(right(71,201));assert.equal(state().selected.size,1);
handlers.pointerdown(right(11,141));handlers.pointermove(right(71,201));
key('Escape',{ctrlKey:false});assert.equal(state().selected.size,0);
handlers.pointerup(right(71,201));assert.equal(state().selected.size,0);
handlers.pointerdown(right(11,141));
handlers.pointermove(right(71,201,{pointerId:2}));
handlers.pointerup(right(71,201,{pointerId:2}));assert.equal(state().selected.size,0);
handlers.pointerup(pointer(71,201));assert.equal(state().selected.size,0); // 왼쪽 버튼 해제 무시
handlers.pointerup(right(71,201));assert.equal(state().selected.size,1);
assert.equal(JSON.stringify(state().strokes),before);assert.equal(state().history.length,1);
''')


def test_rectangle_selection_clamps_to_zoomed_image_when_released_outside_canvas(tmp_path):
    run_js(tmp_path, keyboard_harness() + r'''
render();stroke();
render('pen','user:case-a',null,{zoom:2,crop:[10,20,100,50]});
const right=(x,y)=>({...pointer(x,y),button:2});
handlers.pointerdown(right(2,112));handlers.pointermove(right(1000,1000));
handlers.pointerup(right(1000,1000));assert.equal(state().selected.size,1);
key('c');key('v');assert.deepEqual(state().strokes[1].points,[[12.5,22.5],[32.5,42.5]]);
assert.equal(messages.filter(m=>m.type==='streamlit:setComponentValue').length,0);
''')


def test_right_selection_is_scoped_to_pen_tools_and_preserves_navigation_zoom(tmp_path):
    run_js(tmp_path, keyboard_harness() + r'''
render();stroke();
const right=(x,y)=>({...pointer(x,y),button:2});
for(const extra of [{read_only:true},{ink_enabled:false},{mode:'measure'},{mode:'roi'},{mode:'eraser'}]){
    render('pen','user:case-a',null,extra);
    handlers.pointerdown(right(11,141));handlers.pointermove(right(71,201));handlers.pointerup(right(71,201));
    assert.equal(state().selected.size,0);assert.equal(vm.runInContext('drag',sandbox),null);
}
render();vm.runInContext('args.disabled=true',sandbox);
handlers.pointerdown(right(11,141));handlers.pointerup(right(71,201));assert.equal(state().selected.size,0);
render('navigate','user:case-a',null,{zoom:2,crop:[0,0,100,50]});
handlers.pointerdown(right(41,171));handlers.pointerup(right(41,171));handlers.contextmenu(right(41,171));
const events=messages.filter(m=>m.type==='streamlit:setComponentValue');
assert.equal(events.length,1);assert.equal(events[0].value.kind,'navigate');
assert.equal(events[0].value.zoom,1.5);assert.equal(state().strokes.length,1);
''')


# 기존 뷰어 fixture는 가짜 영상과 오프라인 로그인으로 화면을 연다.
from test_viewer import viewer  # noqa: E402,F401
