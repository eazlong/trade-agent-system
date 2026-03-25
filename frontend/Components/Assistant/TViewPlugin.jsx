"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  createChart,
  CrosshairMode,
  Time,
  BarSeries,
  CandlestickSeries,
  CandlestickData,
  createSeriesMarkers,
  HistogramSeries,
  LineStyle,
} from "lightweight-charts";


// 垂直线插件 - 从TypeScript转换为JavaScript
class VertLinePaneRenderer {
  constructor(x, options) {
    this._x = x;
    this._options = options;
  }

  draw(target) {
    target.useBitmapCoordinateSpace((scope) => {
      if (this._x === null) return;
      const ctx = scope.context;
      const position = this._positionsLine(
        this._x,
        scope.horizontalPixelRatio,
        this._options.width
      );
      ctx.fillStyle = this._options.color;
      ctx.fillRect(
        position.position,
        0,
        position.length,
        scope.bitmapSize.height
      );
    });
  }

  _positionsLine(x, pixelRatio, width) {
    const widthInPixels = Math.max(1, Math.floor(pixelRatio * width));
    const position = Math.round(x * pixelRatio) - Math.floor(widthInPixels / 2);
    return {
      position: position,
      length: widthInPixels,
    };
  }
}

class VertLinePaneView {
  constructor(source, options) {
    this._source = source;
    this._options = options;
    this._x = null;
  }

  update() {
    const timeScale = this._source._chart.timeScale();
    this._x = timeScale.timeToCoordinate(this._source._time);
  }

  renderer() {
    return new VertLinePaneRenderer(this._x, this._options);
  }
}

class VertLineTimeAxisView {
  constructor(source, options) {
    this._source = source;
    this._options = options;
    this._x = null;
  }

  update() {
    const timeScale = this._source._chart.timeScale();
    this._x = timeScale.timeToCoordinate(this._source._time);
  }

  visible() {
    return this._options.showLabel;
  }

  tickVisible() {
    return this._options.showLabel;
  }

  coordinate() {
    return this._x ?? 0;
  }

  text() {
    return this._options.labelText;
  }

  textColor() {
    return this._options.labelTextColor;
  }

  backColor() {
    return this._options.labelBackgroundColor;
  }
}

const defaultOptions = {
  color: "green",
  labelText: "",
  width: 3,
  labelBackgroundColor: "green",
  labelTextColor: "white",
  showLabel: false,
};

class VertLine {
  constructor(chart, series, time, options = {}) {
    const vertLineOptions = {
      ...defaultOptions,
      ...options,
    };
    this._chart = chart;
    this._series = series;
    this._time = time;
    this._paneViews = [new VertLinePaneView(this, vertLineOptions)];
    this._timeAxisViews = [new VertLineTimeAxisView(this, vertLineOptions)];
  }

  updateAllViews() {
    this._paneViews.forEach((pw) => pw.update());
    this._timeAxisViews.forEach((tw) => tw.update());
  }

  timeAxisViews() {
    return this._timeAxisViews;
  }

  paneViews() {
    return this._paneViews;
  }
}

// 水平线插件
class HorizontalLine {
  constructor(chart, series, price, options = {}) {
    this._chart = chart;
    this._series = series;
    this._price = price;
    this._options = {
      color: "red",
      width: 2,
      style: 0, // 0 = solid, 1 = dotted, 2 = dashed
      ...options,
    };

    // 创建价格线
    this._priceLine = this._series.createPriceLine({
      price: this._price,
      color: this._options.color,
      lineWidth: this._options.width,
      lineStyle:
        this._options.style === 1
          ? LineStyle.Dotted
          : this._options.style === 2
          ? LineStyle.Dashed
          : LineStyle.Solid,
      axisLabelVisible: true,
      title: this._options.title || "水平线",
    });
  }

  remove() {
    if (this._priceLine) {
      this._series.removePriceLine(this._priceLine);
    }
  }
}
