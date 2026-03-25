class PriceMeasurementPaneRenderer {
	constructor(p1, p2, options) {
		this._p1 = p1;
		this._p2 = p2;
		this._options = options;
	}

	draw(target) {
		target.useBitmapCoordinateSpace(scope => {
			// Check if points are valid
			if (
				this._p1.x === null ||
				this._p1.y === null ||
				this._p2.x === null ||
				this._p2.y === null
			) {
				return;
			}

			const ctx = scope.context;
			const x1Scaled = Math.round(this._p1.x * scope.horizontalPixelRatio);
			const y1Scaled = Math.round(this._p1.y * scope.verticalPixelRatio);
			const x2Scaled = Math.round(this._p2.x * scope.horizontalPixelRatio);
			const y2Scaled = Math.round(this._p2.y * scope.verticalPixelRatio);

			// Calculate rectangle dimensions
			const rectX = Math.min(x1Scaled, x2Scaled);
			const rectY = Math.min(y1Scaled, y2Scaled);
			const rectWidth = Math.abs(x2Scaled - x1Scaled);
			const rectHeight = Math.abs(y2Scaled - y1Scaled);

			// Draw semi-transparent rectangle area
			ctx.fillStyle = this._options.rectFillColor;
			ctx.fillRect(rectX, rectY, rectWidth, rectHeight);

			// Draw rectangle border
			ctx.lineWidth = this._options.rectBorderWidth;
			ctx.strokeStyle = this._options.rectBorderColor;
			ctx.setLineDash(this._options.lineStyle === 1 ? [5, 5] : this._options.lineStyle === 2 ? [10, 5] : []);
			ctx.strokeRect(rectX, rectY, rectWidth, rectHeight);
			ctx.setLineDash([]); // Reset line dash

			// Draw corner markers (like trading software)
			ctx.fillStyle = this._options.cornerColor;
			const cornerSize = this._options.cornerSize;

			// Top-left corner
			ctx.fillRect(x1Scaled - cornerSize/2, y1Scaled - cornerSize/2, cornerSize, cornerSize);
			// Top-right corner
			ctx.fillRect(x2Scaled - cornerSize/2, y1Scaled - cornerSize/2, cornerSize, cornerSize);
			// Bottom-left corner
			ctx.fillRect(x1Scaled - cornerSize/2, y2Scaled - cornerSize/2, cornerSize, cornerSize);
			// Bottom-right corner
			ctx.fillRect(x2Scaled - cornerSize/2, y2Scaled - cornerSize/2, cornerSize, cornerSize);

			// Draw labels if enabled
			if (this._options.showLabels) {
				this._drawCornerLabels(scope, x1Scaled, y1Scaled, x2Scaled, y2Scaled);
			}
		});
	}

	_drawCornerLabels(scope, x1, y1, x2, y2) {
		const ctx = scope.context;
		const offset = 8 * scope.horizontalPixelRatio;

		// Top-left label (P1)
		if (this._options.label1) {
			ctx.font = '12px Arial';
			const textWidth = ctx.measureText(this._options.label1);
			ctx.fillStyle = this._options.labelBackgroundColor;
			ctx.fillRect(x1 - textWidth.width - offset * 2, y1 - 20, textWidth.width + offset * 2, 16 + offset);
			ctx.fillStyle = this._options.labelTextColor;
			ctx.fillText(this._options.label1, x1 - textWidth.width - offset, y1 - 8);
		}

		// Bottom-right label (P2)
		if (this._options.label2) {
			ctx.font = '12px Arial';
			const textWidth = ctx.measureText(this._options.label2);
			ctx.fillStyle = this._options.labelBackgroundColor;
			ctx.fillRect(x2 + offset, y2 + 4, textWidth.width + offset * 2, 16 + offset);
			ctx.fillStyle = this._options.labelTextColor;
			ctx.fillText(this._options.label2, x2 + offset * 2, y2 + 16);
		}
	}
}

class PriceMeasurementPaneView {
	constructor(source) {
		this._source = source;
		this._p1 = { x: null, y: null };
		this._p2 = { x: null, y: null };
	}

	update() {
		const series = this._source._series;
		const timeScale = this._source._chart.timeScale();

		// Convert first point with error handling
		if (this._source._p1 && this._source._p1.price !== null && this._source._p1.time !== null) {
			try {
				const y1 = series.priceToCoordinate(this._source._p1.price);
				const x1 = timeScale.timeToCoordinate(this._source._p1.time);
				this._p1 = { x: x1, y: y1 };
			} catch (error) {
				console.warn('Error converting first point coordinates:', error);
				this._p1 = { x: null, y: null };
			}
		} else {
			this._p1 = { x: null, y: null };
		}

		// Convert second point with error handling
		if (this._source._p2 && this._source._p2.price !== null && this._source._p2.time !== null) {
			try {
				const y2 = series.priceToCoordinate(this._source._p2.price);
				const x2 = timeScale.timeToCoordinate(this._source._p2.time);
				this._p2 = { x: x2, y: y2 };
			} catch (error) {
				console.warn('Error converting second point coordinates:', error);
				this._p2 = { x: null, y: null };
			}
		} else {
			this._p2 = { x: null, y: null };
		}
	}

	renderer() {
		return new PriceMeasurementPaneRenderer(
			this._p1,
			this._p2,
			this._source._options
		);
	}
}

const defaultOptions = {
	// Rectangle styling
	rectFillColor: 'rgba(19, 49, 243, 0.1)',
	rectBorderColor: 'rgba(19, 49, 243, 0.8)',
	rectBorderWidth: 1,

	// Corner markers
	cornerColor: 'rgba(239, 189, 49, 1)',
	cornerSize: 4,

	// Line styling (for border)
	lineStyle: 0, // 0 = solid, 1 = dotted, 2 = dashed

	// Labels
	showLabels: true,
	labelBackgroundColor: 'rgba(255, 255, 255, 0.85)',
	labelTextColor: 'rgba(0, 0, 0, 1)',

	// Colors for measurement results
	positiveColor: 'rgba(38, 166, 154, 1)', // Green
	negativeColor: 'rgba(239, 83, 80, 1)',  // Red
};

export class PriceMeasurement {
	constructor(chart, series, p1 = null, p2 = null, options = {}) {
		this._chart = chart;
		this._series = series;
		this._p1 = p1;
		this._p2 = p2;
		this._options = {
			...defaultOptions,
			...(options || {}),
		};

		// Initialize labels and measurement values
		this._options.label1 = p1 ? `P1: ${p1.price.toFixed(4)}` : '';
		this._options.label2 = p2 ? `P2: ${p2.price.toFixed(4)}` : '';
		this._options.priceDiff = (p1 && p2) ? p2.price - p1.price : null;
		this._options.percentChange = (p1 && p2 && p1.price !== 0) ? ((p2.price - p1.price) / p1.price) * 100 : null;

		this._paneViews = [new PriceMeasurementPaneView(this)];
	}

	// Required method for lightweight-charts primitives
	autoscaleInfo(startTimePoint, endTimePoint) {
		if (!this._p1 || !this._p2) return null;

		const p1Index = this._pointIndex(this._p1);
		const p2Index = this._pointIndex(this._p2);
		if (p1Index === null || p2Index === null) return null;

		// Check if the line is within the visible time range
		if (endTimePoint < p1Index || startTimePoint > p2Index) return null;

		const minPrice = Math.min(this._p1.price, this._p2.price);
		const maxPrice = Math.max(this._p1.price, this._p2.price);

		return {
			priceRange: {
				minValue: minPrice,
				maxValue: maxPrice,
			},
		};
	}

	// Helper method to get logical index for a point
	_pointIndex(p) {
		if (!p || !p.time) return null;

		try {
			const coordinate = this._chart.timeScale().timeToCoordinate(p.time);
			if (coordinate === null) return null;
			const index = this._chart.timeScale().coordinateToLogical(coordinate);
			return index;
		} catch (error) {
			console.warn('Error getting point index:', error);
			return null;
		}
	}

	// Update point positions
	updatePoints(p1, p2) {
		this._p1 = p1;
		this._p2 = p2;

		// Update labels and measurements
		this._options.label1 = p1 ? `${p1.price.toFixed(4)}` : '';
		this._options.label2 = p2 ? `${p2.price.toFixed(4)}` : '';
		this._options.priceDiff = (p1 && p2) ? p2.price - p1.price : null;
		this._options.percentChange = (p1 && p2 && p1.price !== 0) ? ((p2.price - p1.price) / p1.price) * 100 : null;
	}

	updateAllViews() {
		this._paneViews.forEach(pw => pw.update());
	}

	paneViews() {
		return this._paneViews;
	}
}