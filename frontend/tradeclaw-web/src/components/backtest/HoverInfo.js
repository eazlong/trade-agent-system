const defaultOptions = {
  backgroundColor: 'rgba(17, 24, 39, 0.9)', // tailwind gray-900 with opacity
  borderColor: 'rgba(55, 65, 81, 1)',       // gray-700
  textColor: 'rgba(249, 250, 251, 1)',      // gray-50
  accentPositive: 'rgba(52, 211, 153, 1)',  // green-400
  accentNegative: 'rgba(248, 113, 113, 1)', // red-400
  accentVolume: 'rgba(96, 165, 250, 1)',    // blue-400
  font: '14px Arial',
  lineHeight: 18,
  paddingX: 8,
  paddingY: 4,
  marginX: 4,
  marginY: 4,
};

class HoverInfoPaneRenderer {
  constructor(options) {
    this._options = options;
  }

  draw(target) {
    target.useBitmapCoordinateSpace((scope) => {
      const hover = this._options.hoverData;
      if (!hover) return;

      const ctx = scope.context;
      const horizontalPixelRatio = scope.horizontalPixelRatio;
      const verticalPixelRatio = scope.verticalPixelRatio;

      const paddingX = this._options.paddingX * horizontalPixelRatio;
      const paddingY = this._options.paddingY * verticalPixelRatio;
      const marginX = this._options.marginX * horizontalPixelRatio;
      const marginY = this._options.marginY * verticalPixelRatio;

      ctx.save();

      ctx.font = this._options.font;

      const lines = this._buildLines(hover);

      let maxWidth = 0;
      for (const line of lines) {
        const w = ctx.measureText(line.text).width;
        if (w > maxWidth) maxWidth = w;
      }

      const lineHeight = this._options.lineHeight * verticalPixelRatio;
      const boxWidth = maxWidth + paddingX * 2;
      const boxHeight = lineHeight * lines.length + paddingY * 2;

      const x = marginX;
      const y = marginY;

      ctx.fillStyle = this._options.backgroundColor;
      ctx.strokeStyle = this._options.borderColor;
      ctx.lineWidth = 1 * Math.max(horizontalPixelRatio, verticalPixelRatio);

      if (typeof ctx.roundRect === 'function') {
        ctx.beginPath();
        ctx.roundRect(x, y, boxWidth, boxHeight, 4 * horizontalPixelRatio);
        ctx.fill();
        ctx.stroke();
      } else {
        ctx.fillRect(x, y, boxWidth, boxHeight);
        ctx.strokeRect(x, y, boxWidth, boxHeight);
      }

      let currentY = y + paddingY + lineHeight * 0.8;
      for (const line of lines) {
        ctx.fillStyle = line.color;
        ctx.fillText(line.text, x + paddingX, currentY);
        currentY += lineHeight;
      }

      ctx.restore();
    });
  }

  _buildLines(hover) {
    const lines = [];

    if (hover.time) {
      lines.push({ text: hover.time, color: this._options.textColor });
    }

    lines.push({ text: `O: ${hover.open?.toFixed(4) ?? ''}`, color: this._options.textColor });
    lines.push({ text: `H: ${hover.high?.toFixed(4) ?? ''}`, color: this._options.accentPositive });
    lines.push({ text: `L: ${hover.low?.toFixed(4) ?? ''}`, color: this._options.accentNegative });
    lines.push({ text: `C: ${hover.close?.toFixed(4) ?? ''}`, color: this._options.textColor });

    const changeColor = (hover.change ?? 0) >= 0 ? this._options.accentPositive : this._options.accentNegative;
    const signedChange = hover.change >= 0 ? `+${hover.change.toFixed(4)}` : hover.change.toFixed(4);
    const signedPercent = hover.changePercent >= 0 ? `+${hover.changePercent.toFixed(2)}%` : `${hover.changePercent.toFixed(2)}%`;

    lines.push({ text: `Δ: ${signedChange}`, color: changeColor });
    lines.push({ text: `%: ${signedPercent}`, color: changeColor });

    let volumeText = '';
    if (typeof hover.volume === 'number') {
      if (hover.volume > 1_000_000) {
        volumeText = `${(hover.volume / 1_000_000).toFixed(2)}M`;
      } else if (hover.volume > 1_000) {
        volumeText = `${(hover.volume / 1_000).toFixed(2)}K`;
      } else {
        volumeText = hover.volume.toFixed(2);
      }
    }

    lines.push({ text: `V: ${volumeText}`, color: this._options.accentVolume });

    return lines;
  }
}

class HoverInfoPaneView {
  constructor(source) {
    this._source = source;
  }

  update() {}

  renderer() {
    return new HoverInfoPaneRenderer(this._source._options);
  }
}

export class HoverInfo {
  constructor(chart, series, options = {}) {
    this._chart = chart;
    this._series = series;
    this._options = {
      ...defaultOptions,
      hoverData: null,
      ...(options || {}),
    };
    this._paneViews = [new HoverInfoPaneView(this)];
    this._requestedAnimationFrame = null;
  }

  setHoverData(data) {
    this._options.hoverData = data;
    // 请求下一帧重绘，确保快速响应
    if (!this._requestedAnimationFrame) {
      this._requestedAnimationFrame = requestAnimationFrame(() => {
        this._requestedAnimationFrame = null;
        // 触发 chart 重绘（通过无副作用的 applyOptions）
        if (this._chart) {
          try {
            this._chart.applyOptions({});
          } catch (e) {
            // ignore
          }
        }
      });
    }
  }

  updateAllViews() {
    this._paneViews.forEach((pw) => pw.update());
  }

  paneViews() {
    return this._paneViews;
  }
}
