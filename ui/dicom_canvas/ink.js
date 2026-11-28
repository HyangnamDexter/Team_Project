/* 임시 설명 그림. 좌표와 굵기는 영상 단위이며 서버·원본 파일에 쓰지 않는다. */
class InkLayer {
    constructor() {
        this.token = null;
        this.strokes = [];
        this.history = [];
        this.future = [];
        this.selected = new Set();
        this.clipboard = [];
        this.pasteCount = 0;
        this.translating = null;
        this.active = null;
        this.erasing = null;
        this.lastCommand = null;
    }
    setContext(token) {
        if (this.token === token) return;
        this.token = token;
        this.strokes = [];
        this.history = [];
        this.future = [];
        this.selected = new Set();
        this.clipboard = [];
        this.pasteCount = 0;
        this.translating = null;
        this.active = null;
        this.erasing = null;
        this.lastCommand = null;
    }
    remember(previous) {
        this.history.push(previous);
        if (this.history.length > 50) this.history.shift();
        this.future = [];
    }
    begin(point, color, width, scale) {
        this.cancel();
        this.deselect();
        this.active = {
            color: ['#ff4d4f', '#ffe34d', '#30e878'].includes(color) ? color : '#ff4d4f',
            width: ([2, 4, 8].includes(width) ? width : 4) / scale,
            points: [[...point]],
            spacing: 0.5 / scale
        };
    }
    move(point) {
        if (!this.active || !point || this.active.points.length >= 10000) return;
        const previous = this.active.points.at(-1);
        if (Math.hypot(point[0] - previous[0], point[1] - previous[1]) >= this.active.spacing) {
            this.active.points.push([...point]);
        }
    }
    finish(point) {
        if (!this.active) return;
        this.move(point);
        this.remember(this.strokes);
        this.strokes = [...this.strokes, this.active];
        this.active = null;
    }
    beginErase() {
        this.cancel();
        this.deselect();
        this.erasing = this.strokes;
    }
    erase(from, to, radius) {
        if (!this.erasing) return;
        this.strokes = this.strokes.filter(stroke => {
            const points = stroke.points;
            const distance = radius + stroke.width / 2;
            if (points.length === 1) return InkLayer.pointDistance(points[0], from, to) > distance;
            for (let i = 1; i < points.length; i++) {
                if (InkLayer.segmentDistance(from, to, points[i - 1], points[i]) <= distance) return false;
            }
            return true;
        });
    }
    finishErase() {
        if (!this.erasing) return;
        if (this.strokes.length !== this.erasing.length) this.remember(this.erasing);
        this.erasing = null;
    }
    cancel() {
        this.active = null;
        if (this.erasing) this.strokes = this.erasing;
        this.erasing = null;
        if (this.translating) {
            this.strokes = this.translating.before;
            this.selected = this.translating.selected;
            this.translating = null;
        }
    }
    undo() {
        this.cancel();
        if (this.history.length) {
            this.future.push(this.strokes);
            this.strokes = this.history.pop();
            this.deselect();
        }
    }
    redo() {
        this.cancel();
        if (this.future.length) {
            this.history.push(this.strokes);
            this.strokes = this.future.pop();
            this.deselect();
        }
    }
    clear() {
        this.cancel();
        if (this.strokes.length) {
            this.remember(this.strokes);
            this.strokes = [];
        }
        this.deselect();
    }
    deselect() {
        this.selected = new Set();
    }
    selectAll() {
        this.selected = new Set(this.strokes);
    }
    bounds(strokes = [...this.selected]) {
        if (!strokes.length) return null;
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (const stroke of strokes) for (const [x,y] of stroke.points) {
            x0 = Math.min(x0,x); y0 = Math.min(y0,y);
            x1 = Math.max(x1,x); y1 = Math.max(y1,y);
        }
        return [x0,y0,x1,y1];
    }
    hitSelected(point, tolerance) {
        const b = this.bounds();
        return b && point[0] >= b[0]-tolerance && point[0] <= b[2]+tolerance &&
                    point[1] >= b[1]-tolerance && point[1] <= b[3]+tolerance;
    }
    selectAt(point, tolerance, additive = false) {
        const hit = [...this.strokes].reverse().find(stroke => {
            const p = stroke.points;
            const radius = tolerance + stroke.width/2;
            if (p.length === 1) return Math.hypot(point[0]-p[0][0],point[1]-p[0][1]) <= radius;
            return p.slice(1).some((end,i) => InkLayer.pointDistance(point,p[i],end) <= radius);
        });
        if (!additive) this.deselect();
        if (hit) {
            if (additive && this.selected.has(hit)) this.selected.delete(hit);
            else this.selected.add(hit);
        }
    }
    selectRect(start, end, additive = false) {
        const x0 = Math.min(start[0],end[0]), y0 = Math.min(start[1],end[1]);
        const x1 = Math.max(start[0],end[0]), y1 = Math.max(start[1],end[1]);
        const corners = [[x0,y0],[x1,y0],[x1,y1],[x0,y1]];
        const inside = ([x,y]) => x >= x0 && x <= x1 && y >= y0 && y <= y1;
        if (!additive) this.deselect();
        for (const stroke of this.strokes) {
            const radius = stroke.width/2;
            const points = stroke.points;
            // 꼭짓점만 보지 않고 선분까지 검사해, 영역을 가로지르는 긴 선도 선택한다.
            const touches = points.some(inside) || corners.some((corner,i) => {
                const edgeEnd = corners[(i+1)%4];
                if (points.length === 1) return InkLayer.pointDistance(points[0],corner,edgeEnd) <= radius;
                return points.slice(1).some((end,j) =>
                    InkLayer.segmentDistance(points[j],end,corner,edgeEnd) <= radius);
            });
            if (touches) this.selected.add(stroke);
        }
    }
    copy() {
        if (!this.selected.size) return;
        // 영상·정답·탐지 박스 대신 선택한 펜 선만 내부 메모리에 복사한다.
        this.clipboard = this.strokes.filter(s => this.selected.has(s)).map(s =>
            ({...s,points:s.points.map(p => [...p])}));
        this.pasteCount = 0;
    }
    translation(bounds, dx, dy, cols, rows) {
        return [Math.max(-bounds[0],Math.min(cols-1-bounds[2],dx)),
                Math.max(-bounds[1],Math.min(rows-1-bounds[3],dy))];
    }
    paste(cols, rows, offset) {
        if (!this.clipboard.length) return;
        const distance = ++this.pasteCount * offset;
        const [dx,dy] = this.translation(this.bounds(this.clipboard),distance,distance,cols,rows);
        const copies = this.clipboard.map(s => ({...s,points:s.points.map(([x,y]) => [x+dx,y+dy])}));
        this.remember(this.strokes);
        this.strokes = [...this.strokes,...copies];
        this.selected = new Set(copies);
    }
    deleteSelected() {
        if (!this.selected.size) return;
        this.remember(this.strokes);
        this.strokes = this.strokes.filter(s => !this.selected.has(s));
        this.deselect();
    }
    beginTranslate() {
        this.cancel();
        if (this.selected.size) this.translating = {
            before:this.strokes, selected:this.selected, dx:0, dy:0
        };
    }
    translate(dx, dy, cols, rows) {
        const move = this.translating;
        if (!move) return;
        [dx,dy] = this.translation(this.bounds([...move.selected]),dx,dy,cols,rows);
        move.dx = dx; move.dy = dy;
        const selected = new Set();
        this.strokes = move.before.map(s => {
            if (!move.selected.has(s)) return s;
            const copy = {...s,points:s.points.map(([x,y]) => [x+dx,y+dy])};
            selected.add(copy);
            return copy;
        });
        this.selected = selected;
    }
    finishTranslate() {
        const move = this.translating;
        if (!move) return;
        if (move.dx || move.dy) this.remember(move.before);
        else {
            this.strokes = move.before;
            this.selected = move.selected;
        }
        this.translating = null;
    }
    applyCommand(command) {
        if (!command || command.id === this.lastCommand) return;
        this.lastCommand = command.id;
        if (command.action === 'undo') this.undo();
        if (command.action === 'redo') this.redo();
        if (command.action === 'clear') this.clear();
    }
    draw(ctx, area, crop, scale) {
        ctx.save();
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        for (const stroke of [...this.strokes, ...(this.active ? [this.active] : [])]) {
            const points = stroke.points.map(p => [area.x + (p[0] + 0.5 - crop[0]) * scale,
                                                  area.y + (p[1] + 0.5 - crop[1]) * scale]);
            ctx.strokeStyle = stroke.color;
            ctx.fillStyle = stroke.color;
            ctx.lineWidth = stroke.width * scale;
            ctx.beginPath();
            if (points.length === 1) {
                ctx.arc(...points[0], ctx.lineWidth / 2, 0, 2 * Math.PI);
                ctx.fill();
            } else {
                ctx.moveTo(...points[0]);
                points.slice(1).forEach(p => ctx.lineTo(...p));
                ctx.stroke();
            }
        }
        const bounds = this.bounds();
        if (bounds) {
            ctx.strokeStyle = '#55d6ff'; ctx.lineWidth = 1;
            ctx.setLineDash([5,4]);
            ctx.strokeRect(area.x+(bounds[0]+0.5-crop[0])*scale-4,
                           area.y+(bounds[1]+0.5-crop[1])*scale-4,
                           (bounds[2]-bounds[0])*scale+8,(bounds[3]-bounds[1])*scale+8);
        }
        ctx.restore();
    }
    static pointDistance(p, a, b) {
        const dx = b[0] - a[0], dy = b[1] - a[1];
        const length = dx * dx + dy * dy;
        const t = length ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length)) : 0;
        return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
    }
    static segmentDistance(a, b, c, d) {
        const cross = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
        if (cross(a, b, c) * cross(a, b, d) < 0 && cross(c, d, a) * cross(c, d, b) < 0) return 0;
        return Math.min(this.pointDistance(a, c, d), this.pointDistance(b, c, d),
                        this.pointDistance(c, a, b), this.pointDistance(d, a, b));
    }
}
if (typeof module !== 'undefined') module.exports = {InkLayer};
