/* 임시 설명 그림. 좌표와 굵기는 영상 단위이며 서버·원본 파일에 쓰지 않는다. */
class InkLayer {
    constructor() {
        this.token = null;
        this.strokes = [];
        this.history = [];
        this.active = null;
        this.erasing = null;
        this.lastCommand = null;
    }
    setContext(token) {
        if (this.token === token) return;
        this.token = token;
        this.strokes = [];
        this.history = [];
        this.active = null;
        this.erasing = null;
        this.lastCommand = null;
    }
    remember(previous) {
        this.history.push(previous);
        if (this.history.length > 50) this.history.shift();
    }
    begin(point, color, width, scale) {
        this.cancel();
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
    }
    undo() {
        this.cancel();
        if (this.history.length) this.strokes = this.history.pop();
    }
    clear() {
        this.cancel();
        if (this.strokes.length) {
            this.remember(this.strokes);
            this.strokes = [];
        }
    }
    applyCommand(command) {
        if (!command || command.id === this.lastCommand) return;
        this.lastCommand = command.id;
        if (command.action === 'undo') this.undo();
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
