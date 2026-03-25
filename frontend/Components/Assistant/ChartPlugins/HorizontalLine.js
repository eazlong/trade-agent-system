class HorizontalLinePaneRenderer {
	constructor(p1, p2, options) {
		this._p1 = p1;
		this._p2 = p2;
		this._options = options;
	}

	draw(target) {
		target.useBitmapCoordinateSpace(scope => {
			if (
				this._p1.x === null ||
				this._p1.y === null ||
				this._p2.x === null ||
				this._p2.y === null
			)
				return;
			const ctx = scope.context;
			const x1Scaled = Math.round(this._p1.x * scope.horizontalPixelRatio);
			const y1Scaled = Math.round(this._p1.y * scope.verticalPixelRatio);
			const x2Scaled = Math.round(this._p2.x * scope.horizontalPixelRatio);
			const y2Scaled = Math.round(this._p2.y * scope.verticalPixelRatio);
			ctx.lineWidth = this._options.width;
			ctx.strokeStyle = this._options.lineColor;
			ctx.beginPath();
			ctx.moveTo(x1Scaled, y1Scaled);
			ctx.lineTo(x2Scaled, y2Scaled);
			ctx.stroke();
		});
	}
}

class HorizontalLinePaneView {
	constructor(source) {
		this._source = source;
		this._price = { x: null, y: null };
	}

	update() {
		const series = this._source._series;
		const y = series.priceToCoordinate(this._source._price);
		this._p1 = { x: 0, y: y };
		this._p2 = { x: 1000000000, y: y };
	}

	renderer() {
		return new HorizontalLinePaneRenderer(
			this._p1,
			this._p2,
			this._source._options
		);
	}

}


// 水平线插件
export class HorizontalLine {
    constructor(chart, series, price, options = {}) {
      this._chart = chart;
      this._series = series;
      this._price = price;
      this._options = {
        lineColor: "rgb(19, 49, 243)",
        width: 1,
        style: 0, // 0 = solid, 1 = dotted, 2 = dashed
        ...options,
      };

      this._paneViews = [new HorizontalLinePaneView(this)];
    }
  
    // autoscaleInfo(startTimePoint, endTimePoint) {
	// 	const p1Index = this._pointIndex(this._p1);
	// 	const p2Index = this._pointIndex(this._p2);
	// 	if (p1Index === null || p2Index === null) return null;
	// 	if (endTimePoint < p1Index || startTimePoint > p2Index) return null;
	// 	return {
	// 		priceRange: {
	// 			minValue: this._minPrice,
	// 			maxValue: this._maxPrice,
	// 		},
	// 	};
	// }

	updateAllViews() {
		this._paneViews.forEach(pw => pw.update());
	}

	paneViews() {
		return this._paneViews;
	}

	// _pointIndex(p) {
	// 	const coordinate = this._chart
	// 		.timeScale()
	// 		.timeToCoordinate(p.time);
	// 	if (coordinate === null) return null;
	// 	const index = this._chart.timeScale().coordinateToLogical(coordinate);
	// 	return index;
	// }
}