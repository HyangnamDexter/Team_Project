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
        pen_command:command,...extra}}});
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
    viewer.button(key="pen_clear").click().run()
    assert viewer.session_state["viewer_pen_command"] == {"id": sequence + 1, "action": "clear"}
    assert viewer.session_state["viewer_points"] == [[1, 2], [4, 6]]
    assert viewer.metric[0].value == "5.00 px"


def test_other_roles_do_not_get_pen_controls(viewer):
    viewer.session_state["user"] = {"uid": "manager", "role": "manager", "email": "manager@test.com"}
    viewer.run()
    assert not viewer.exception
    assert "펜" not in viewer.radio(key="viewer_tool").options
    assert not any(b.key == "pen_clear" for b in viewer.button)


# 기존 뷰어 fixture는 가짜 영상과 오프라인 로그인으로 화면을 연다.
from test_viewer import viewer  # noqa: E402,F401
