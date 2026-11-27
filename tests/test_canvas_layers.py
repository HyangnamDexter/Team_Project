"""실제 canvas JS의 crop 좌표 변환을 Node로 검증한다."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_canvas_boxes_follow_zoom_and_pan():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js가 없는 환경")
    html = (Path(__file__).resolve().parents[1] / "ui/dicom_canvas/index.html").read_text(encoding="utf-8")
    script = html.split("<script>")[1].split("</script>")[0]
    harness = r'''
const vm = require('vm');
let rectangles = [];
const ctx = new Proxy({}, {get: (o,k) => k === 'measureText' ? s=>({width:s.length*6}) :
    (...v)=>{if(k==='strokeRect') rectangles.push(v)}});
const canvas = {parentElement:{clientWidth:400},style:{},getContext:()=>ctx,addEventListener:()=>{}};
const sandbox = {document:{querySelector:()=>canvas},devicePixelRatio:1,
    window:{parent:{postMessage:()=>{}},addEventListener:()=>{}}};
vm.createContext(sandbox);
vm.runInContext(SOURCE,sandbox);
vm.runInContext(`args={max_height:420,crop:[0,0,200,100],points:[],boxes:[
    {xmin:100,ymin:50,xmax:120,ymax:60,color:'#ffe34d',label:'model'}]};image={};draw();`,sandbox);
const before = rectangles.pop(); rectangles=[];
vm.runInContext(`args.crop=[100,50,100,50];draw();`,sandbox);
console.log(JSON.stringify({before,after:rectangles.pop()}));
'''
    code = "const SOURCE=" + json.dumps(script) + ";\n" + harness
    result = subprocess.run([node, "-e", code], capture_output=True, text=True, check=True)
    geometry = json.loads(result.stdout)
    assert geometry["before"] == [200, 210, 40, 20]
    assert geometry["after"] == [0, 110, 80, 40]
