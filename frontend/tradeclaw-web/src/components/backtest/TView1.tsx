// pages/index.tsx
"use client";

import React, { useEffect, useRef, useState, useCallback, useMemo } from "react";
import {
  createChart,
  CrosshairMode,
  ColorType,
  CandlestickSeries,
  createSeriesMarkers,
  HistogramSeries,
} from "lightweight-charts";

import { getUserLines, createUserLines, updateUserLines, deleteUserLines } from "services/assistant.service";

import { TrendLine } from "./ChartPlugins/TrendLine";
import { HorizontalLine } from "./ChartPlugins/HorizontalLine";
import { PriceMeasurement } from "./ChartPlugins/PriceMeasurement";
import { HoverInfo } from "./ChartPlugins/HoverInfo";
import { getKlines } from "services/trade.service";
import { useLanguage } from "contexts/LanguageContext";
import { side } from "Utils/order";
import { debounce, rafThrottle } from "Utils/performance";
import { usePerformanceMonitor } from "hooks/usePerformanceMonitor";
import { Capacitor } from '@capacitor/core';
import { ScreenOrientation } from '@capacitor/screen-orientation';

// ─── Interfaces ───────────────────────────────────────────────────────────────

interface SubOrder {
  timestamp: string;
  price: number | string;
  opt?: string;
  [key: string]: unknown;
}

interface Order {
  symbol: string;
  timestamp: string;
  first_order_id: string;
  parent_order?: SubOrder[];
  [key: string]: unknown;
}

interface TViewProps {
  order: Order;
}

interface PriceDataItem {
  timestamp: number;
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  [key: string]: unknown;
}

interface VolumeDataItem {
  time: number;
  value: number;
  color: string;
}

interface DrawingPoint {
  time: number;
  price: number;
}

interface LinesState {
  id: number;
  h_lines: string[] | string;
  v_lines: string[] | string;
}

interface PriceMeasurementResultData {
  point1: DrawingPoint;
  point2: DrawingPoint;
  priceDiff: number;
  percentChange: number;
}

interface TimeframeOption {
  value: string;
  label: string;
}

interface DrawingToolOption {
  value: string;
  label: string;
}

interface HoverData {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  change: number;
  changePercent: number;
  time: string;
}

// Extended document interface for vendor-prefixed fullscreen methods
interface FullscreenDocument extends Document {
  webkitExitFullscreen?: () => Promise<void>;
  msExitFullscreen?: () => Promise<void>;
}

interface FullscreenHTMLElement extends HTMLDivElement {
  webkitRequestFullscreen?: () => Promise<void>;
  msRequestFullscreen?: () => Promise<void>;
}

// ─── Utility Functions ────────────────────────────────────────────────────────

// 计算点到线段的垂直距离
function pointToLineDistance(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const A = px - x1;
  const B = py - y1;
  const C = x2 - x1;
  const D = y2 - y1;

  const dot = A * C + B * D;
  const lenSq = C * C + D * D;
  let param = -1;

  if (lenSq !== 0) {
    param = dot / lenSq;
  }

  let xx: number, yy: number;

  if (param < 0) {
    xx = x1;
    yy = y1;
  } else if (param > 1) {
    xx = x2;
    yy = y2;
  } else {
    xx = x1 + param * C;
    yy = y1 + param * D;
  }

  const dx = px - xx;
  const dy = py - yy;
  return Math.sqrt(dx * dx + dy * dy);
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function TView({ order }: TViewProps) {
  const { t } = useLanguage();
  const chartContainerRef = useRef<HTMLDivElement | null>(null);
  const chartWrapperRef = useRef<FullscreenHTMLElement | null>(null);
  const chart = useRef<any>(null);
  const candlestickSeries = useRef<any>(null);
  const [priceData, setPriceData] = useState<PriceDataItem[]>([]);
  const [volumeData, setVolumeData] = useState<VolumeDataItem[]>([]);
  const [timeframe, setTimeframe] = useState<string>("15m");
  const [drawingMode, setDrawingMode] = useState<boolean>(false);
  const [drawingTool, setDrawingTool] = useState<string>("horizontal"); // trendline, horizontal, vertical
  const [drawingPoints, setDrawingPoints] = useState<DrawingPoint[]>([]);
  const drawingPointsRef = useRef<DrawingPoint[]>([]);
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const [isLandscape, setIsLandscape] = useState<boolean>(false);
  const isFullscreenRef = useRef<boolean>(false);
  const [horizontalLines, setHorizontalLines] = useState<number[]>([]);
  const [trendLines, setTrendLines] = useState<DrawingPoint[][]>([]);
  const trendLinesRef = useRef<DrawingPoint[][]>([]);
  const trendPrimitivesRef = useRef<any[]>([]);
  const [lines, setLines] = useState<LinesState>({ id: 0, h_lines: [], v_lines: [] });
  const [selectedLine, setSelectedLine] = useState<number | null>(null);
  const [selectedTrendLine, setSelectedTrendLine] = useState<number | null>(null); // 选中的趋势线索引
  const selectedTrendLineRef = useRef<number | null>(null);
  const [isLoadingData, setIsLoadingData] = useState<boolean>(false);
  const [isLoadingKlines, setIsLoadingKlines] = useState<boolean>(false);
  const [dataRange, setDataRange] = useState<{ start: number; end: number }>({ start: 0, end: 0 });
  const [chartInitialized, setChartInitialized] = useState<boolean>(false);
  // Price measurement tool state
  const [priceMeasurementMode, setPriceMeasurementMode] = useState<boolean>(false);
  const [priceMeasurementPoints, setPriceMeasurementPoints] = useState<DrawingPoint[]>([]);
  const [priceMeasurementResult, setPriceMeasurementResult] = useState<PriceMeasurementResultData | null>(null);
  // 鼠标悬浮状态
  // const [hoveredData, setHoveredData] = useState(null);
  const linesRef = useRef<LinesState>(lines);
  const horizontalLinesRef = useRef<number[]>(horizontalLines);
  const horizontalPrimitivesRef = useRef<any[]>([]); // Store primitive references
  const drawingModeRef = useRef<boolean>(drawingMode);
  const priceDataRef = useRef<PriceDataItem[]>(priceData);
  const volumeDataRef = useRef<VolumeDataItem[]>(volumeData);
  const dataRangeRef = useRef<{ start: number; end: number }>(dataRange);
  const visibleRangeRef = useRef<any>(null);
  const priceMeasurementModeRef = useRef<boolean>(priceMeasurementMode);
  const priceMeasurementPointsRef = useRef<DrawingPoint[]>(priceMeasurementPoints);
  const priceMeasurementPrimitiveRef = useRef<any>(null);
  const isLoadingDataRef = useRef<boolean>(isLoadingData);
  const histogramSeriesRef = useRef<any>(null);
  const chartInitializedRef = useRef<boolean>(false);
  const hoverInfoPrimitiveRef = useRef<any>(null);
  const timeframeRef = useRef<string>(timeframe);
  const drawingToolRef = useRef<string>(drawingTool)
  const isMobile: boolean = Capacitor.isNativePlatform();
  const trendPreviewPrimitiveRef = useRef<any>(null); // 趋势线实时预览

  // Performance monitoring for the chart component
  const performanceMetrics = usePerformanceMonitor({
    name: 'TView',
    logRenders: process.env.NODE_ENV === 'development',
    warnThreshold: 16, // 60fps threshold
  });

  useEffect(() => {
    linesRef.current = lines;
    horizontalLinesRef.current = horizontalLines;
    trendLinesRef.current = trendLines;
    drawingPointsRef.current = drawingPoints;
  }, [lines, horizontalLines, trendLines, drawingPoints]);

  useEffect(() => {
    priceDataRef.current = priceData;
    volumeDataRef.current = volumeData;
    dataRangeRef.current = dataRange;
  }, [priceData, volumeData, dataRange]);

  useEffect(() => {
    isLoadingDataRef.current = isLoadingData;
  }, [isLoadingData]);

  useEffect(() => {
    drawingToolRef.current = drawingTool;
  }, [drawingTool])

  useEffect(() => {
    chartInitializedRef.current = chartInitialized;
  }, [chartInitialized]);

  useEffect(() => {
    timeframeRef.current = timeframe;
  }, [timeframe]);

  useEffect(() => {
    priceMeasurementModeRef.current = priceMeasurementMode;
  }, [priceMeasurementMode]);

  useEffect(() => {
    priceMeasurementPointsRef.current = priceMeasurementPoints;
  }, [priceMeasurementPoints]);

  useEffect(() => {
    drawingModeRef.current = drawingMode;
  }, [drawingMode]);

  useEffect(() => {
    selectedTrendLineRef.current = selectedTrendLine;
  }, [selectedTrendLine]);

  // 删除选中的趋势线
  const deleteSelectedTrendLine = (): void => {
    if (selectedTrendLineRef.current === null) return;

    const index = selectedTrendLineRef.current;

    // 从图表移除
    if (trendPrimitivesRef.current[index] && candlestickSeries.current) {
      try {
        candlestickSeries.current.detachPrimitive(trendPrimitivesRef.current[index]);
      } catch (error) {
        console.warn('Error detaching trend primitive:', error);
      }
    }

    // 从 ref 移除
    trendPrimitivesRef.current.splice(index, 1);
    // 从 state 移除
    setTrendLines(prev => prev.filter((_, i) => i !== index));

    setSelectedTrendLine(null);
  };

  // 动态加载更多数据的函数
  const loadMoreData = useCallback(async (direction: string = 'earlier'): Promise<void> => {
    if (!order || !order.symbol || isLoadingDataRef.current) return;

    try {
      const currentData = priceDataRef.current;
      const interval = timeframe.endsWith("m") ? 1000 * 60 : timeframe.endsWith("h") ? 1000 * 60 * 60 : 1000 * 60 * 60 * 24;
      const timeInterval = parseInt(timeframe.slice(0, -1)) * interval;

      if (direction === 'later' && priceDataRef.current[priceDataRef.current.length - 1].timestamp > new Date().getTime() - timeInterval) {
        console.log('No more later data available');
        return;
      }

      setIsLoadingData(true);

      if (currentData.length === 0) return;

      let startTime: number, endTime: number;
      const batchSize = 1000; // 每次请求1000个数据点

      if (direction === 'earlier') {
        // 请求更早的数据
        endTime = currentData[0].timestamp - timeInterval;
        startTime = endTime - (batchSize - 1) * timeInterval;
      } else {
        // 请求更新的数据
        startTime = currentData[currentData.length - 1].timestamp + timeInterval;
        endTime = startTime + (batchSize - 1) * timeInterval;
      }

      console.log(`Loading ${direction} data:`, new Date(startTime).toLocaleString(), 'to', new Date(endTime).toLocaleString());

      const newData: PriceDataItem[] = await getKlines(
        order.symbol,
        timeframe,
        startTime,
        endTime
      );

      if (newData.length === 0) {
        console.log(`No more ${direction} data available`);
        return;
      }

      // 为新数据添加 time 字段 (转换为秒级时间戳)
      newData.forEach((item: PriceDataItem) => {
        item.time = item.timestamp;
      });

      // 合并数据并排序
      let mergedPriceData: PriceDataItem[];
      if (direction === 'earlier') {
        mergedPriceData = [...newData, ...currentData];
      } else {
        mergedPriceData = [...currentData, ...newData];
      }

      // 确保数据按时间戳排序（升序）
      mergedPriceData.sort((a, b) => a.timestamp - b.timestamp);

      // 去除重复数据
      const uniqueData = mergedPriceData.filter((item, index, arr) => {
        if (index === 0) return true;
        return item.timestamp !== arr[index - 1].timestamp;
      });

      // 处理交易量数据
      const volumes: VolumeDataItem[] = uniqueData.map(item => ({
        time: item.timestamp, // 使用秒级时间戳保持一致性
        value: item.volume > 90071992547409.91 ? 90071992547409.91 : item.volume,
        color: item.close > item.open ? '#00d4ff' : '#ef5350'
      }));

      // 直接更新图表数据而不触发重新渲染
      if (chart.current && candlestickSeries.current && chartInitializedRef.current) {
        try {
          // 保存当前可视范围
          const currentVisibleRange = chart.current.timeScale().getVisibleRange();

          // 更新数据
          candlestickSeries.current.setData(uniqueData);

          // 更新交易量数据
          if (histogramSeriesRef.current) {
            histogramSeriesRef.current.setData(volumes);
          }

          // 立即恢复可视范围
          if (visibleRangeRef.current) {
            chart.current.timeScale().setVisibleRange(visibleRangeRef.current);
          } else if (currentVisibleRange) {
            chart.current.timeScale().setVisibleRange(currentVisibleRange);
          }

          // console.log(`Loaded ${newData.length} ${direction} data points, total: ${uniqueData.length}`);

          // 更新引用数据但不触发状态更新
          priceDataRef.current = uniqueData;
          volumeDataRef.current = volumes;

          return;

        } catch (error) {
          console.warn('Error updating chart data directly:', error);
        }
      }
    } catch (error) {
      console.error(`Error loading ${direction} data:`, error);
    } finally {
      setIsLoadingData(false);
    }
  }, [order, timeframe]);

  useEffect(() => {
    if (!order || !order.first_order_id) return;

    setDrawingMode(false);
    setHorizontalLines([]);
    setTrendLines([]);
    setDrawingPoints([]);
    setLines({ id: 0, h_lines: [], v_lines: [] });

    const controller = new AbortController();

    const fetchUserLines = async () => {
      try {
        const res: any = await getUserLines(order.first_order_id, { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (res && res.data) {
          setLines(res.data);
          // Handle empty or null h_lines
          const hLines = res.data.h_lines;
          if (hLines && typeof hLines === 'string' && hLines.trim()) {
            setHorizontalLines(hLines.split(",").filter((line: string) => line.trim()).map(Number));
          } else {
            setHorizontalLines([]);
          }
        }
      } catch (err: any) {
        if (err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED' || err?.message === 'canceled') return;

        // Handle 404 - User lines don't exist yet, which is normal for new orders
        if (err?.response?.status === 404) {
          console.log('User lines not found for order', order.first_order_id, '- this is normal for new orders');
          setLines({ id: 0, h_lines: [], v_lines: [] });
          setHorizontalLines([]);
        } else {
          console.error('Error fetching user lines:', err);
        }
      }
    };
    fetchUserLines();

    return () => {
      controller.abort();
    }
  }, [order]);

  const saveLines = useCallback(async (newHorizontalLines: number[]): Promise<void> => {
    try {
      if (!order || !order.first_order_id) {
        console.error('Cannot save lines: Order or order_id is missing');
        return;
      }

      const linesData = newHorizontalLines.filter(line => line && !isNaN(line)).join(",");

      if (linesRef.current.id > 0) {
        const res = await updateUserLines(linesRef.current.id, {
          h_lines: linesData
        });
        if (res && res.data) {
          setLines(res.data);
        }
      } else {
        const res = await createUserLines({
          order_id: order.first_order_id,
          h_lines: linesData
        });
        if (res && res.data) {
          setLines(res.data);
        }
      }
    } catch (error: any) {
      console.error('Error saving user lines:', error);
      // Show user-friendly error message
      if (error?.response?.status === 404) {
        console.log('Order not found, unable to save lines');
      } else if (error?.response?.status >= 500) {
        console.log('Server error, please try again later');
      }
    }
  }, [order]);

  // 检测是否为移动设备和屏幕方向
  useEffect(() => {
    const checkMobileAndOrientation = (): void => {
      let landscape = window.innerWidth > window.innerHeight;
      // 使用Screen Orientation API检测方向（如果可用）
      if (screen.orientation) {
        landscape = screen.orientation.type.includes('landscape');
      }

      setIsLandscape(landscape);
    };

    const handleOrientationChange = (): void => {
      if (screen.orientation) {
        setIsLandscape(screen.orientation.type.includes('landscape'));
      } else {
        setIsLandscape(window.innerWidth > window.innerHeight);
      }
    };

    checkMobileAndOrientation();
    window.addEventListener('resize', checkMobileAndOrientation);

    // 监听屏幕方向变化
    if (screen.orientation) {
      screen.orientation.addEventListener('change', handleOrientationChange);
    }

    return () => {
      window.removeEventListener('resize', checkMobileAndOrientation);
      if (screen.orientation) {
        screen.orientation.removeEventListener('change', handleOrientationChange);
      }
    };
  }, []);

  useEffect(() => {
    const fullscreen = async (): Promise<void> => {
      //进入全屏
      const el = chartWrapperRef.current as FullscreenHTMLElement | null;
      if (el?.requestFullscreen) {
        await el.requestFullscreen();
      } else if (el?.webkitRequestFullscreen) {
        await el.webkitRequestFullscreen();
      } else if (el?.msRequestFullscreen) {
        await el.msRequestFullscreen();
      }

      isFullscreenRef.current = true;
      setIsFullscreen(true);
    }

    if (screen.orientation.type.startsWith("landscape") && isMobile) {
      console.log(`orientation: ${screen.orientation.type}`);
      fullscreen()
    }
  }, [screen.orientation])

  // 全屏切换功能
  const toggleFullscreen = useCallback(async (): Promise<void> => {
    if (!document.fullscreenElement) {
      console.log(`orientation: ${screen.orientation.type}`);
      try {
        if (!chartWrapperRef.current || !chartWrapperRef.current.isConnected) {
          console.error('进入全屏失败: Element not connected');
          return;
        }

        if (isMobile) {
          await ScreenOrientation.lock({
            orientation: 'landscape'
          });
        } else {
          //进入全屏
          if (chartWrapperRef.current.requestFullscreen) {
            await chartWrapperRef.current.requestFullscreen();
          } else if (chartWrapperRef.current.webkitRequestFullscreen) {
            await chartWrapperRef.current.webkitRequestFullscreen();
          } else if (chartWrapperRef.current.msRequestFullscreen) {
            await chartWrapperRef.current.msRequestFullscreen();
          }
        }


        // isFullscreenRef.current = true;
        // setIsFullscreen(true);

      } catch (error) {
        console.error('进入全屏失败:', error);
      }
    } else {
      try {
        const doc = document as FullscreenDocument;
        if (doc.exitFullscreen) {
          await doc.exitFullscreen();
        } else if (doc.webkitExitFullscreen) {
          await doc.webkitExitFullscreen();
        } else if (doc.msExitFullscreen) {
          await doc.msExitFullscreen();
        }

        // isFullscreenRef.current = false;
        // setIsFullscreen(false);
        if (isMobile) {
          await ScreenOrientation.lock({
            orientation: 'portrait'
          });
        }
      } catch (error) {
        console.error('退出全屏失败:', error);
      }
    }
  }, [isMobile]);

  // 二分查找最接近目标时间戳的数据点
  const binarySearchClosest = (data: PriceDataItem[], targetTime: number): PriceDataItem | null => {
    if (!data || data.length === 0) return null;
    let left = 0;
    let right = data.length - 1;

    while (left < right) {
      const mid = Math.floor((left + right) / 2);
      if (data[mid].timestamp < targetTime) {
        left = mid + 1;
      } else {
        right = mid;
      }
    }

    // 检查 left 和 left-1，返回更接近的那个
    if (left > 0) {
      const diffLeft = Math.abs(data[left].timestamp - targetTime);
      const diffPrev = Math.abs(data[left - 1].timestamp - targetTime);
      if (diffPrev < diffLeft) {
        return data[left - 1];
      }
    }
    return data[left];
  };

  // 处理鼠标悬浮事件，显示K线信息（优化版：直接操作 primitive，跳过 React 状态）
  const handleCrosshairMove = useCallback((param: any): void => {
    // 处理趋势线实时预览 - 当用户选择了第一个点并正在绘制趋势线时
    if (drawingModeRef.current && drawingToolRef.current === 'trendline' && drawingPointsRef.current.length === 1 && param && param.point && candlestickSeries.current) {
      // 获取鼠标当前位置的坐标
      const point = param.point;
      const time = param.time;
      const price = candlestickSeries.current.coordinateToPrice(point.y);

      if (price !== null && price !== undefined) {
        const p1 = drawingPointsRef.current[0];
        const p2 = { time, price };

        if (!trendPreviewPrimitiveRef.current && chart.current && candlestickSeries.current) {
          // 创建实时预览趋势线（从p1到p2的实线线段，跟随鼠标转动）
          const trend = new TrendLine(chart.current, candlestickSeries.current, p1, p2, {
            lineColor: 'rgb(19, 49, 243)',
            width: 2,
            isRealTimePreview: true, // 启用实时预览模式
          });
          candlestickSeries.current.attachPrimitive(trend);
          trendPreviewPrimitiveRef.current = trend;
        } else if (trendPreviewPrimitiveRef.current) {
          // 更新实时预览趋势线
          trendPreviewPrimitiveRef.current.updateSecondPoint(p2);
        }
      }
    }

    // 处理价格测量模式的实时预览
    if (priceMeasurementModeRef.current && priceMeasurementPointsRef.current.length === 1 && param && param.point && param.time && candlestickSeries.current) {
      // 直接使用 param.time（毫秒时间戳，与图表数据格式一致）
      const time = param.time;
      const price = candlestickSeries.current.coordinateToPrice(param.point.y);
      if (price !== null && price !== undefined) {
        const p1 = priceMeasurementPointsRef.current[0];
        const p2 = { time, price };

        // 如果还没有 primitive，创建一个
        if (!priceMeasurementPrimitiveRef.current && chart.current && candlestickSeries.current) {
          const measurement = new (PriceMeasurement as any)(chart.current, candlestickSeries.current, p1, p2);
          candlestickSeries.current.attachPrimitive(measurement);
          priceMeasurementPrimitiveRef.current = measurement;
        } else if (priceMeasurementPrimitiveRef.current) {
          // 更新现有 primitive 的第二个点
          priceMeasurementPrimitiveRef.current.updatePoints(p1, p2);
        }
      }
    }

    // 处理 HoverInfo
    if (!param || !param.time || !hoverInfoPrimitiveRef.current) {
      if (hoverInfoPrimitiveRef.current) {
        hoverInfoPrimitiveRef.current.setHoverData(null);
      }
      return;
    }

    const data = priceDataRef.current;
    if (!data || data.length === 0) {
      hoverInfoPrimitiveRef.current.setHoverData(null);
      return;
    }

    // 使用二分查找快速定位
    const closestData = binarySearchClosest(data, param.time);
    if (!closestData) {
      hoverInfoPrimitiveRef.current.setHoverData(null);
      return;
    }

    // 计算涨跌幅
    const change = closestData.close - closestData.open;
    const changePercent = (change / closestData.open) * 100;

    // 直接更新 primitive，不走 React 状态
    hoverInfoPrimitiveRef.current.setHoverData({
      timestamp: closestData.timestamp,
      open: closestData.open,
      high: closestData.high,
      low: closestData.low,
      close: closestData.close,
      volume: closestData.volume,
      change,
      changePercent,
      time: new Date(closestData.timestamp).toLocaleString()
    } as HoverData);
  }, [binarySearchClosest]);

  // 清除所有绘图
  useEffect(() => {
    return () => {
      clearAllDrawings();
    };
  }, [order]);

  // 重置悬浮信息当订单变化时
  // useEffect(() => {
  //   setHoveredData(null);
  // }, [order]);

  // 处理鼠标点击事件，用于画线
  const handleChartClick = useCallback((param: any): void => {
    if (!param.point || !candlestickSeries.current) return;

    try {
      // Handle price measurement mode
      if (priceMeasurementModeRef.current) {
        // If measurement result is already displayed, clicking anywhere exits measurement mode
        if (priceMeasurementPointsRef.current.length === 2) {
          console.log('priceMeasurementResult', priceMeasurementResult);
          setPriceMeasurementMode(false);
          setPriceMeasurementPoints([]);
          setPriceMeasurementResult(null);

          // Remove measurement primitive
          if (priceMeasurementPrimitiveRef.current && candlestickSeries.current) {
            try {
              candlestickSeries.current.detachPrimitive(priceMeasurementPrimitiveRef.current);
            } catch (error) {
              console.warn('Error detaching price measurement primitive:', error);
            }
            priceMeasurementPrimitiveRef.current = null;
          }
          return;
        }

        // 直接使用 param.time（毫秒时间戳，与图表数据格式一致）
        const time = param.time;
        const price = candlestickSeries.current.coordinateToPrice(param.point.y);
        if (price === null || price === undefined) return;

        const newPoint: DrawingPoint = { time, price };
        const newPoints = [...priceMeasurementPointsRef.current, newPoint];
        setPriceMeasurementPoints(newPoints);

        // If we have two points, finalize the measurement
        if (newPoints.length === 2) {
          // Calculate price difference and percentage change
          const priceDiff = newPoints[1].price - newPoints[0].price;
          const percentChange = ((newPoints[1].price - newPoints[0].price) / newPoints[0].price) * 100;

          // Store the result (for state tracking)
          setPriceMeasurementResult({
            point1: newPoints[0],
            point2: newPoints[1],
            priceDiff,
            percentChange
          });

          // 如果 primitive 已经存在（从 crosshairMove 创建），只需更新最终位置
          if (priceMeasurementPrimitiveRef.current) {
            priceMeasurementPrimitiveRef.current.updatePoints(newPoints[0], newPoints[1]);
          } else if (chart.current && candlestickSeries.current) {
            // 如果不存在，创建一个新的
            const measurement = new (PriceMeasurement as any)(chart.current, candlestickSeries.current, newPoints[0], newPoints[1]);
            candlestickSeries.current.attachPrimitive(measurement);
            priceMeasurementPrimitiveRef.current = measurement;
          }
        }
        return;
      }

      // Handle regular drawing mode
      if (!drawingModeRef.current) {
        const price = candlestickSeries.current.coordinateToPrice(param.point.y);
        if (price === null || price === undefined) return;

        // 检测是否点击了水平线
        const exist = horizontalLinesRef.current.filter(line => (line <= price * 1.005 && line >= price * 0.995));
        if (exist.length > 0) {
          setSelectedLine(exist[0]);
          setSelectedTrendLine(null); // 取消趋势线选中
          return;
        }

        // 检测是否点击了趋势线
        const clickedX = param.point.x;
        const clickedY = param.point.y;

        for (let i = 0; i < trendLinesRef.current.length; i++) {
          const points = trendLinesRef.current[i];
          if (points.length === 2) {
            const p1 = points[0];
            const p2 = points[1];

            // 获取两点在屏幕上的坐标
            const timeScale = chart.current.timeScale();
            const x1 = timeScale.timeToCoordinate(p1.time);
            const x2 = timeScale.timeToCoordinate(p2.time);
            const y1 = candlestickSeries.current.priceToCoordinate(p1.price);
            const y2 = candlestickSeries.current.priceToCoordinate(p2.price);

            if (x1 === null || x2 === null || y1 === null || y2 === null) continue;

            // 计算点到线段的距离
            const distance = pointToLineDistance(clickedX, clickedY, x1, y1, x2, y2);
            const threshold = 10; // 10像素的容差范围

            if (distance < threshold) {
              setSelectedTrendLine(i);
              setSelectedLine(null); // 取消水平线选中
              return;
            }
          }
        }

        // 如果没有点击任何线条，取消所有选中
        setSelectedLine(null);
        setSelectedTrendLine(null);
        return;
      }

      console.log("handleChartClick", param);

      const time = param.time;
      const price = candlestickSeries.current.coordinateToPrice(param.point.y);
      if (price === null || price === undefined) return;

      if (drawingToolRef.current === "horizontal") {
        // const horizontal = new HorizontalLine(chart.current, candlestickSeries.current, price);
        const newHorizontalLines = [...horizontalLinesRef.current, price];
        setHorizontalLines(newHorizontalLines);
        saveLines(newHorizontalLines);

        setDrawingMode(false);
      }
      else if (drawingToolRef.current === "trendline") {
        const newPoint: DrawingPoint = { time, price };

        if (drawingPointsRef.current.length === 0) {
          // 第一个点：直接存储并等待预览
          setDrawingPoints([newPoint]);
          console.log('First point added:', newPoint);
        } else if (drawingPointsRef.current.length === 1) {
          // 第二个点：创建最终趋势线
          const p1 = drawingPointsRef.current[0];
          const p2 = newPoint;

          // 移除实时预览线
          if (trendPreviewPrimitiveRef.current && candlestickSeries.current) {
            try {
              candlestickSeries.current.detachPrimitive(trendPreviewPrimitiveRef.current);
            } catch (e) { /* ignore */ }
            trendPreviewPrimitiveRef.current = null;
          }

          console.log('Creating trendline between:', p1, 'and', p2);
          const newTrendLines = [...trendLinesRef.current, [p1, p2]];
          setTrendLines(newTrendLines);
          const trend = new TrendLine(chart.current, candlestickSeries.current, p1, p2);
          candlestickSeries.current.attachPrimitive(trend);
          trendPrimitivesRef.current.push(trend);
          console.log('Trendline created and attached, total trendlines:', newTrendLines.length);
          setDrawingPoints([]);
          setDrawingMode(false);
        }
      }
    } catch (error) {
      console.warn('Error handling chart click:', error);
    }
  }, [saveLines, pointToLineDistance, priceMeasurementResult]);

  // 清除所有绘图
  const clearAllDrawings = useCallback((): void => {
    try {
      setDrawingPoints([]);
      setPriceMeasurementPoints([]);
      setPriceMeasurementResult(null);

      // Only try to detach primitives if candlestickSeries is still valid
      if (candlestickSeries.current) {
        for (const horizontalLine of horizontalPrimitivesRef.current) {
          try {
            candlestickSeries.current.detachPrimitive(horizontalLine);
          } catch (error) {
            console.warn('Error detaching primitive:', error);
          }
        }

        for (const trendLine of trendPrimitivesRef.current) {
          try {
            candlestickSeries.current.detachPrimitive(trendLine);
          } catch (error) {
            console.warn('Error detaching trend primitive:', error);
          }
        }

        // Detach price measurement primitive if exists
        if (priceMeasurementPrimitiveRef.current) {
          try {
            candlestickSeries.current.detachPrimitive(priceMeasurementPrimitiveRef.current);
          } catch (error) {
            console.warn('Error detaching price measurement primitive:', error);
          }
          priceMeasurementPrimitiveRef.current = null;
        }

        // Detach trendline preview primitive if exists
        if (trendPreviewPrimitiveRef.current) {
          try {
            candlestickSeries.current.detachPrimitive(trendPreviewPrimitiveRef.current);
          } catch (error) {
            console.warn('Error detaching trendline preview primitive:', error);
          }
          trendPreviewPrimitiveRef.current = null;
        }
      }

      setHorizontalLines([]);
      setTrendLines([]);
      horizontalPrimitivesRef.current = [];
      trendPrimitivesRef.current = [];
    } catch (error) {
      console.warn('Error clearing drawings:', error);
    }
  }, []);

  // 获取K线数据
  useEffect(() => {
    if (!order || !order.symbol || !order.timestamp) {
      console.warn('Order data is incomplete, cannot fetch Klines data');
      return;
    }

    // 重置图表状态
    setChartInitialized(false);
    visibleRangeRef.current = null;
    setPriceData([]);
    setVolumeData([]);
    const interval = timeframe.endsWith("m") ? 1000 * 60 : timeframe.endsWith("h") ? 1000 * 60 * 60 : 1000 * 60 * 60 * 24;
    const time = parseInt(timeframe.slice(0, -1)) * interval;

    // 计算订单组中所有订单的时间范围
    const orderTime = new Date(order.timestamp).getTime();
    let minTime = orderTime;
    let maxTime = orderTime;

    if (order.parent_order && order.parent_order.length > 0) {
      order.parent_order.forEach(o => {
        const t = new Date(o.timestamp).getTime();
        if (t < minTime) minTime = t;
        if (t > maxTime) maxTime = t;
      });
    }

    // 确保 start_time <= end_time
    const start_time = minTime;
    const end_time = maxTime;
    const count = Math.max(1, (end_time - start_time) / time);
    const add = count > 1000 ? 100 : parseInt(String((1000 - count) / 2));

    const controller = new AbortController();

    // 保存请求发起时的timeframe，用于验证响应
    const requestTimeframe = timeframe;

    const fetchData = async (): Promise<void> => {
      setIsLoadingKlines(true);
      try {
        let temp: PriceDataItem[] = [];
        let left = count + 2 * add;
        let start = start_time - add * time;
        let end = end_time + add * time;

        console.log("time", new Date(start).toLocaleString(), new Date(end).toLocaleString());
        while (left >= 1) {
          if (controller.signal.aborted) return;
          // 检查timeframe是否已经变化
          if (timeframeRef.current !== requestTimeframe) {
            console.log('Timeframe changed during fetch, aborting');
            return;
          }
          try {
            const data: PriceDataItem[] = await getKlines(
              order.symbol,
              timeframe,
              start,
              end,
              { signal: controller.signal }
            );

            if (controller.signal.aborted) return;
            // 再次检查timeframe是否变化
            if (timeframeRef.current !== requestTimeframe) {
              console.log('Timeframe changed after fetch, discarding data');
              return;
            }
            if (data.length == 0) {
              break;
            }
            start = data[data.length - 1].timestamp + time;
            left -= data.length;
            data.map(item => {
              item.time = item.timestamp;
            });
            temp.push(...data);
          } catch (error: any) {
            if (error?.name === 'CanceledError' || error?.code === 'ERR_CANCELED' || error?.message === 'canceled') {
              return;
            }
            console.error('Error fetching price data:', error);
            break;
          }
        }
        if (controller.signal.aborted) return;
        // 最终检查，确保数据与当前timeframe匹配
        if (timeframeRef.current !== requestTimeframe) {
          console.log('Timeframe changed, discarding fetched data');
          return;
        }
        setPriceData(temp);
        // 处理交易量数据
        const volumes: VolumeDataItem[] = temp.map(item => ({
          time: item.timestamp, // 使用秒级时间戳保持一致性
          value: item.volume > 90071992547409.91 ? 90071992547409.91 : item.volume,
          color: item.close > item.open ? '#00d4ff' : '#ef5350'
        }));
        setVolumeData(volumes);
      } finally {
        // 只有当前请求的timeframe仍然匹配时才重置加载状态
        // 如果timeframe已变化，新的请求会处理加载状态
        if (timeframeRef.current === requestTimeframe) {
          setIsLoadingKlines(false);
        }
      }
    };
    fetchData();


    return () => {
      controller.abort();
    }
  }, [order, timeframe]);


  // 创建图表
  useEffect(() => {
    if (!chartContainerRef.current) return;
    if (!chartWrapperRef.current) return;
    if (priceData.length === 0) return;

    // 如果图表已经初始化，则更新数据而不是重新创建
    if (chartInitializedRef.current && chart.current && candlestickSeries.current) {
      try {
        candlestickSeries.current.setData(priceData);
        if (histogramSeriesRef.current) {
          histogramSeriesRef.current.setData(volumeData);
        }
        return;
      } catch (error) {
        console.warn('Error updating chart data:', error);
      }
    }

    // 如果图表已经初始化，则不重新创建
    if (chartInitializedRef.current && chart.current) {
      return;
    }

    // 保存可视范围（如果存在）
    let visibleRange = visibleRangeRef.current;
    if (!visibleRange && chart.current) {
      try {
        visibleRange = chart.current.timeScale().getVisibleRange();
        visibleRangeRef.current = visibleRange;
      } catch (error) {
        console.warn('Error getting visible range during chart recreation:', error);
      }
    }

    // 清理现有图表
    if (chart.current) {
      try {
        chart.current.remove();
      } catch (error) {
        console.warn('Error removing existing chart:', error);
      }
    }
    chart.current = createChart(chartContainerRef.current, {
      width: chartWrapperRef.current.clientWidth,
      height: isFullscreenRef.current
        ? window.innerHeight - 64
        : chartWrapperRef.current.clientHeight > 64
          ? chartWrapperRef.current.clientHeight - 64
          : window.innerHeight - 180,
      layout: {
        background: {
          type: ColorType.Solid,
          color: '#0a0e27',
        },
        textColor: '#a0b0d0',
      },
      grid: {
        vertLines: {
          color: 'rgba(0, 212, 255, 0.06)',
          style: 0,
          visible: true,
        },
        horzLines: {
          color: 'rgba(0, 212, 255, 0.06)',
          style: 0,
          visible: true,
        },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: 'rgba(0, 212, 255, 0.5)',
          width: 1,
          style: 2,
          labelBackgroundColor: 'rgba(0, 212, 255, 0.8)',
        },
        horzLine: {
          color: 'rgba(0, 212, 255, 0.5)',
          width: 1,
          style: 2,
          labelBackgroundColor: 'rgba(0, 212, 255, 0.8)',
        },
      },
      timeScale: {
        borderColor: 'rgba(0, 212, 255, 0.2)',
        tickMarkFormatter: (time: number) => {
          const date = new Date(time);
          const year = date.getFullYear().toString();
          const month = (date.getMonth() + 1).toString().padStart(2, '0');
          const day = date.getDate().toString().padStart(2, '0');
          return `${year}-${month}-${day}`;
        }
      },
      rightPriceScale: {
        borderColor: 'rgba(0, 212, 255, 0.2)',
      },
      localization: {
        timeFormatter: (time: number) => {
          const date = new Date(time);
          const month = (date.getMonth() + 1).toString().padStart(2, '0');
          const day = date.getDate().toString().padStart(2, '0');
          const hours = date.getHours().toString().padStart(2, '0');
          const minutes = date.getMinutes().toString().padStart(2, '0');
          const seconds = date.getSeconds().toString().padStart(2, '0');
          return `${month}-${day} ${hours}:${minutes}:${seconds}`;
        }
      }
    });

    const getMaxDecimalLength = (obj: PriceDataItem): number => {
      return (['open', 'close', 'high', 'low'] as const)
        .map(key => {
          const val = obj[key];
          if (typeof val !== 'number') return 0;
          const parts = val.toString().split('.');
          return parts[1]?.length || 0;
        })
        .reduce((max, len) => Math.max(max, len), 0);
    };

    const precision = priceData.length > 0 ? getMaxDecimalLength(priceData[0]) : 2;
    const seriesOptions = {
      upColor: "#00d4ff",
      downColor: "#ef5350",
      borderVisible: false,
      borderUpColor: "#00d4ff",
      borderDownColor: "#ef5350",
      wickUpColor: "#00d4ff",
      wickDownColor: "#ef5350",
      priceFormat: {
        type: "price" as const,
        precision: precision,
        minMove: 10 ** (-precision)
      },
    };

    candlestickSeries.current = chart.current.addSeries(
      CandlestickSeries,
      seriesOptions
    );

    candlestickSeries.current.setData(priceData);

    const volumeSeries = chart.current.addSeries(HistogramSeries, {
      priceScaleId: "left",
    });
    chart.current.priceScale("left").applyOptions({
      scaleMargins: {
        top: 0.9,
        bottom: 0,
      },
    });
    volumeSeries.setData(volumeData);

    // 保存直方图系列的引用以便后续直接更新
    histogramSeriesRef.current = volumeSeries;

    // 创建并附加 HoverInfo primitive，用于在图表内部显示悬浮 HLOCV 信息
    const hoverInfo = new HoverInfo(chart.current, candlestickSeries.current);
    candlestickSeries.current.attachPrimitive(hoverInfo);
    hoverInfoPrimitiveRef.current = hoverInfo;

    // 标记图表已初始化
    setChartInitialized(true);

    if (order) {
      const interval = timeframe.endsWith("m") ? 1000 * 60 : timeframe.endsWith("h") ? 1000 * 60 * 60 : 1000 * 60 * 60 * 24;
      const timeInterval = parseInt(timeframe.slice(0, -1)) * interval;

      // 将订单时间戳对齐到K线柱的起始时间
      const alignToKlineBar = (timestamp: string): number => {
        const ts = new Date(timestamp).getTime();
        return Math.floor(ts / timeInterval) * timeInterval;
      };

      let markers: any[] = [];
      if (order.parent_order) {
        const parentMarkers = order.parent_order.map((o) => {
          const opt = o.opt || side(o);
          return {
            time: alignToKlineBar(o.timestamp),
            position: "aboveBar",
            color: opt === "Short" || opt === "Close Short" ? "#ef5350" : "#00d4ff",
            shape: "arrowDown",
            text: `${opt} @ ${parseFloat(String(o.price)).toFixed(precision)} `,
          };
        });
        markers = [...markers, ...parentMarkers];
      }

      // 按时间排序确保标记正确显示
      markers.sort((a, b) => a.time - b.time);

      createSeriesMarkers(candlestickSeries.current, markers);
    }
    if (visibleRange) {
      chart.current.timeScale().setVisibleRange(visibleRange);
    } else {
      chart.current.timeScale().fitContent();
    }
    // 添加点击事件监听器用于画线
    chart.current.subscribeClick(handleChartClick);

    // 添加鼠标悬浮事件监听器
    chart.current.subscribeCrosshairMove(handleCrosshairMove);

    // 监听可见范围变化，用于动态加载数据
    const handleVisibleTimeRangeChange = (newVisibleRange: any): void => {
      if (!newVisibleRange || !priceDataRef.current.length || isLoadingDataRef.current) return;

      const currentData = priceDataRef.current;
      const dataStart = currentData[0].timestamp;
      const dataEnd = currentData[currentData.length - 1].timestamp;
      const visibleStart = newVisibleRange.from;
      const visibleEnd = newVisibleRange.to;

      // 计算时间间隔
      const interval = timeframe.endsWith("m") ? 1000 * 60 : timeframe.endsWith("h") ? 1000 * 60 * 60 : 1000 * 60 * 60 * 24;
      const timeInterval = parseInt(timeframe.slice(0, -1)) * interval;

      // 判断是否需要加载更早的数据（用户向左滚动到数据边界）
      const needEarlierData = visibleStart <= dataStart - 100 * timeInterval; // 当可见范围接近数据开始时

      // 判断是否需要加载更新的数据（用户向右滚动到数据边界）
      const needLaterData = visibleEnd >= dataEnd - 100 * timeInterval; // 当可见范围接近数据结束时

      if (needEarlierData) {
        console.log('Loading earlier data due to scroll');
        loadMoreData('earlier');
      } else if (needLaterData) {
        console.log('Loading later data due to scroll');
        loadMoreData('later');
      }
    };

    chart.current.timeScale().subscribeVisibleTimeRangeChange(handleVisibleTimeRangeChange);

    chart.current.timeScale().subscribeVisibleLogicalRangeChange((range: any) => {
      if (!range) return;

      const barsVisible = range.to - range.from;

      if (barsVisible > 1000) {
        // 计算最新区间，只保留最多 1000 根
        const newFrom = range.to - 1000;
        chart.current.timeScale().setVisibleLogicalRange({ from: newFrom, to: range.to });
      }
    })


    return () => {
      if (chart.current) {
        try {
          if (candlestickSeries.current && hoverInfoPrimitiveRef.current) {
            try {
              candlestickSeries.current.detachPrimitive(hoverInfoPrimitiveRef.current);
            } catch (error) {
              console.warn('Error detaching hover info primitive:', error);
            }
            hoverInfoPrimitiveRef.current = null;
          }
          chart.current.unsubscribeClick(handleChartClick);
          chart.current.unsubscribeCrosshairMove(handleCrosshairMove);
          chart.current.timeScale().unsubscribeVisibleTimeRangeChange(handleVisibleTimeRangeChange);
          chart.current.timeScale().unsubscribeVisibleLogicalRangeChange();
          chart.current.remove();
        } catch (error) {
          console.warn('Error cleaning up chart:', error);
        } finally {
          chart.current = null;
          candlestickSeries.current = null;
          histogramSeriesRef.current = null;
          setChartInitialized(false);
        }
        setPriceData([]);
        setVolumeData([]);
        // setHoveredData(null);
      }
    };
  }, [priceData, volumeData, timeframe, order]);

  // 绘制水平线
  useEffect(() => {
    if (!chart.current || !candlestickSeries.current) return;

    handleChartResize()

    try {
      console.log("Drawing horizontal lines", horizontalLinesRef.current);
      horizontalPrimitivesRef.current.forEach(
        primitive => candlestickSeries.current.detachPrimitive(primitive)
      );
      horizontalPrimitivesRef.current = [];

      for (const horizontalLine of horizontalLinesRef.current) {
        const horizontal = new HorizontalLine(chart.current, candlestickSeries.current, horizontalLine, { lineColor: selectedLine == horizontalLine ? "rgb(239, 189, 49)" : "rgb(19, 49, 243)", width: 1, style: 0 });
        if (candlestickSeries.current) {
          candlestickSeries.current.attachPrimitive(horizontal);
          horizontalPrimitivesRef.current.push(horizontal);
          horizontal.updateAllViews();
        }
      }

      // Only access timeScale if chart is still valid
      if (chart.current && chart.current.timeScale) {
        const currentRange = chart.current.timeScale().getVisibleRange();
        if (currentRange) {
          chart.current.timeScale().setVisibleRange(currentRange);
        }
      }
    } catch (error) {
      console.warn('Error drawing horizontal lines:', error);
    }
  }, [horizontalLines, selectedLine, candlestickSeries.current]);

  // 绘制趋势线
  useEffect(() => {
    if (!chart.current || !candlestickSeries.current || !chartInitialized) return;

    try {
      // 清除旧的趋势线
      trendPrimitivesRef.current.forEach(primitive => {
        try {
          candlestickSeries.current.detachPrimitive(primitive);
        } catch (error) {
          console.warn('Error detaching trend primitive:', error);
        }
      });
      trendPrimitivesRef.current = [];

      // 重新绘制所有趋势线
      trendLinesRef.current.forEach((points, index) => {
        if (points.length === 2) {
          const isSelected = selectedTrendLineRef.current === index;
          const trend = new TrendLine(chart.current, candlestickSeries.current, points[0], points[1], {
            lineColor: isSelected ? 'rgb(239, 189, 49)' : 'rgb(19, 49, 243)',
            width: isSelected ? 3 : 2,
          });
          candlestickSeries.current.attachPrimitive(trend);
          trendPrimitivesRef.current.push(trend);
        }
      });
    } catch (error) {
      console.warn('Error drawing trend lines:', error);
    }
  }, [trendLines, selectedTrendLine, chartInitialized, candlestickSeries.current]);

  // Update price measurement when price data changes
  useEffect(() => {
    if (priceMeasurementPrimitiveRef.current) {
      try {
        priceMeasurementPrimitiveRef.current.updateAllViews();
      } catch (error) {
        console.warn('Error updating price measurement views:', error);
      }
    }
  }, [priceData]);

  const handleChartResize = useCallback((): void => {
    try {
      const height = isFullscreenRef.current
        ? window.innerHeight
        : chartWrapperRef.current!.clientHeight > 64
          ? chartWrapperRef.current!.clientHeight - 64
          : window.innerHeight - 180;

      const width = isFullscreenRef.current ? chartContainerRef.current!.clientWidth - (isMobile ? 64 : 32) : chartContainerRef.current!.clientWidth

      if (chart.current && typeof chart.current.resize === 'function') {
        chart.current.resize(width, height);
      }
    } catch (error) {
      console.warn('Error resizing chart on fullscreen change:', error);
    }
  }, [isMobile]);

  // 监听全屏状态变化
  useEffect(() => {
    const handleFullscreenChange = (): void => {
      const newFullscreenState = !!document.fullscreenElement;
      isFullscreenRef.current = newFullscreenState;
      setIsFullscreen(newFullscreenState);

      // 全屏状态变化时调整图表大小
      if (chart.current && chartWrapperRef.current && chartContainerRef.current) {
        handleChartResize()
      }
    };

    document.addEventListener('fullscreenchange', handleFullscreenChange);
    document.addEventListener('webkitfullscreenchange', handleFullscreenChange);
    document.addEventListener('msfullscreenchange', handleFullscreenChange);
    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
      document.removeEventListener('webkitfullscreenchange', handleFullscreenChange);
      document.removeEventListener('msfullscreenchange', handleFullscreenChange);
    };
  }, []);

  const timeframeOptions: TimeframeOption[] = useMemo(() => [
    { value: "1m", label: t('tview.timeframe1m') },
    // { value: "3m", label: "3分" },
    { value: "5m", label: t('tview.timeframe5m') },
    { value: "15m", label: t('tview.timeframe15m') },
    { value: "1h", label: t('tview.timeframe1h') },
    { value: "4h", label: t('tview.timeframe4h') },
    { value: "1d", label: t('tview.timeframe1d') },
  ], [t]);

  const drawingTools: DrawingToolOption[] = useMemo(() => [
    { value: "trendline", label: t('tview.drawingToolTrendline') },
    { value: "horizontal", label: t('tview.drawingToolHorizontal') },
  ], [t]);

  // 定位到当前订单功能
  const locateToOrder = useCallback((): void => {
    if (!chart.current || !candlestickSeries.current || !order || !order.timestamp) {
      console.warn('Cannot locate to order: chart not ready or order data missing');
      return;
    }

    try {
      const orderTime = new Date(order.timestamp).getTime();
      const currentData = priceDataRef.current;

      if (currentData.length === 0) {
        console.warn('No chart data available');
        return;
      }

      // 计算时间间隔（基于当前时间周期）
      const interval = timeframe.endsWith("m") ? 1000 * 60 : timeframe.endsWith("h") ? 1000 * 60 * 60 : 1000 * 60 * 60 * 24;
      const timeInterval = parseInt(timeframe.slice(0, -1)) * interval;

      // 设置可视范围，让订单时间在中间位置
      // 显示前后各50个时间周期
      const rangeSize = 50 * timeInterval;
      const visibleRange = {
        from: orderTime - rangeSize,
        to: orderTime + rangeSize
      };

      console.log('Locating to order time:', new Date(orderTime).toLocaleString(), 'Range:', new Date(visibleRange.from).toLocaleString(), 'to', new Date(visibleRange.to).toLocaleString());

      chart.current.timeScale().setVisibleRange(visibleRange);

      // 可选：添加视觉反馈，比如闪烁标记
      // 这里我们可以通过重新绘制标记来实现
    } catch (error) {
      console.error('Error locating to order:', error);
    }
  }, [order, timeframe]);

  return (
    <div
      ref={chartWrapperRef as React.RefObject<HTMLDivElement>}
      style={{
        width: '100%',
        height: '100%',
        background: 'var(--space-bg)',
        display: 'flex',
        flexDirection: isFullscreenRef.current && isLandscape ? 'row-reverse' : 'column',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Control Panel */}
      <div
        style={{
          display: 'flex',
          flexDirection: isFullscreenRef.current && isLandscape ? 'column' : 'row',
          gap: '8px',
          padding: '12px',
          background: 'rgba(10, 14, 39, 0.6)',
          backdropFilter: 'blur(20px)',
          borderBottom: isFullscreenRef.current && isLandscape ? 'none' : '1px solid var(--space-border)',
          borderLeft: isFullscreenRef.current && isLandscape ? '1px solid var(--space-border)' : 'none',
          zIndex: 10,
          flexShrink: 0,
          overflowX: 'auto',
          overflowY: isFullscreenRef.current && isLandscape ? 'auto' : 'visible',
        }}
      >
        {/* Timeframe Selector */}
        <div
          style={{
            display: 'flex',
            flexDirection: isFullscreenRef.current && isLandscape ? 'column' : 'row',
            gap: '6px',
            alignItems: 'center',
          }}
        >
          <span
            style={{
              color: 'var(--space-accent)',
              fontSize: '10px',
              textTransform: 'uppercase',
              letterSpacing: '0.1em',
              fontFamily: 'Orbitron, sans-serif',
              opacity: 0.7,
              whiteSpace: 'nowrap',
            }}
          >
            Timeframe
          </span>
          <div style={{ display: 'flex', flexDirection: isFullscreenRef.current && isLandscape ? 'column' : 'row', gap: '6px' }}>
            {timeframeOptions.map((option) => (
              <button
                key={option.value}
                disabled={isLoadingKlines && timeframe !== option.value}
                style={{
                  padding: '6px 12px',
                  fontSize: '11px',
                  fontFamily: 'Orbitron, sans-serif',
                  fontWeight: 600,
                  letterSpacing: '0.05em',
                  textTransform: 'uppercase',
                  borderRadius: '8px',
                  border: `2px solid ${timeframe === option.value ? 'var(--space-accent)' : 'var(--space-border-light)'}`,
                  background: timeframe === option.value
                    ? 'var(--space-blue-dark)'
                    : 'rgba(0, 212, 255, 0.05)',
                  color: timeframe === option.value
                    ? 'var(--space-primary)'
                    : 'var(--space-secondary)',
                  cursor: (isLoadingKlines && timeframe !== option.value) ? 'not-allowed' : 'pointer',
                  opacity: (isLoadingKlines && timeframe !== option.value) ? 0.5 : 1,
                  transition: 'all 0.3s ease',
                  boxShadow: timeframe === option.value
                    ? '0 0 20px var(--space-accent-glow)'
                    : 'none',
                }}
                onClick={() => {
                  if (!isLoadingKlines) {
                    setTimeframe(option.value);
                  }
                }}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        {/* Divider */}
        <div
          style={{
            width: isFullscreenRef.current && isLandscape ? 'auto' : '1px',
            height: isFullscreenRef.current && isLandscape ? '1px' : 'auto',
            background: 'var(--space-border)',
            margin: isFullscreenRef.current && isLandscape ? '4px 0' : '0 8px',
          }}
        />

        {/* Drawing Tools */}
        <div
          style={{
            display: 'flex',
            flexDirection: isFullscreenRef.current && isLandscape ? 'column' : 'row',
            gap: '6px',
            alignItems: 'center',
          }}
        >
          <span
            style={{
              color: 'var(--space-accent)',
              fontSize: '10px',
              textTransform: 'uppercase',
              letterSpacing: '0.1em',
              fontFamily: 'Orbitron, sans-serif',
              opacity: 0.7,
              whiteSpace: 'nowrap',
            }}
          >
            Tools
          </span>
          <div style={{ display: 'flex', flexDirection: isFullscreenRef.current && isLandscape ? 'column' : 'row', gap: '6px' }}>
            {drawingTools.map((tool) => (
              <button
                key={tool.value}
                style={{
                  padding: '6px 12px',
                  fontSize: '11px',
                  fontFamily: 'Orbitron, sans-serif',
                  fontWeight: 600,
                  letterSpacing: '0.05em',
                  textTransform: 'uppercase',
                  borderRadius: '8px',
                  border: `2px solid ${drawingMode && drawingTool === tool.value ? 'var(--space-accent)' : 'var(--space-border-light)'}`,
                  background: drawingMode && drawingTool === tool.value
                    ? 'var(--space-blue-dark)'
                    : 'rgba(0, 212, 255, 0.05)',
                  color: drawingMode && drawingTool === tool.value
                    ? 'var(--space-primary)'
                    : 'var(--space-secondary)',
                  cursor: 'pointer',
                  transition: 'all 0.3s ease',
                  boxShadow: drawingMode && drawingTool === tool.value
                    ? '0 0 20px var(--space-accent-glow)'
                    : 'none',
                }}
                onClick={() => {
                  // 清除之前的预览线
                  if (trendPreviewPrimitiveRef.current && candlestickSeries.current) {
                    try {
                      candlestickSeries.current.detachPrimitive(trendPreviewPrimitiveRef.current);
                    } catch (e) { /* ignore */ }
                    trendPreviewPrimitiveRef.current = null;
                  }
                  setDrawingPoints([]);
                  setDrawingTool(tool.value);
                  setDrawingMode(true);
                }}
              >
                {tool.label}
              </button>
            ))}
          </div>
        </div>

        {/* Divider */}
        <div
          style={{
            width: isFullscreenRef.current && isLandscape ? 'auto' : '1px',
            height: isFullscreenRef.current && isLandscape ? '1px' : 'auto',
            background: 'var(--space-border)',
            margin: isFullscreenRef.current && isLandscape ? '4px 0' : '0 8px',
          }}
        />

        {/* Action Buttons */}
        <div
          style={{
            display: 'flex',
            flexDirection: isFullscreenRef.current && isLandscape ? 'column' : 'row',
            gap: '6px',
            alignItems: 'center',
            marginLeft: isFullscreenRef.current && isLandscape ? 'auto' : 0,
          }}
        >
          {/* Clear Drawings Button */}
          <button
            style={{
              padding: '6px 12px',
              fontSize: '11px',
              fontFamily: 'Orbitron, sans-serif',
              fontWeight: 600,
              letterSpacing: '0.05em',
              textTransform: 'uppercase',
              borderRadius: '8px',
              border: '2px solid rgba(239, 68, 68, 0.5)',
              background: 'linear-gradient(135deg, rgba(239, 68, 68, 0.2) 0%, rgba(220, 38, 38, 0.2) 100%)',
              color: '#f87171',
              cursor: 'pointer',
              transition: 'all 0.3s ease',
            }}
            onClick={() => {
              // 清除趋势线预览线
              if (trendPreviewPrimitiveRef.current && candlestickSeries.current) {
                try {
                  candlestickSeries.current.detachPrimitive(trendPreviewPrimitiveRef.current);
                } catch (e) { /* ignore */ }
                trendPreviewPrimitiveRef.current = null;
              }
              deleteUserLines(lines.id).then((res: any) => {
                console.log("res", res);
              }).catch((err: any) => {
                console.error('Error deleting user lines:', err);
              });
              clearAllDrawings();
              setLines({ id: 0, h_lines: [], v_lines: [] });
              setHorizontalLines([]);
              setPriceMeasurementMode(false);
              setPriceMeasurementPoints([]);
              setPriceMeasurementResult(null);
              if (priceMeasurementPrimitiveRef.current && candlestickSeries.current) {
                try {
                  candlestickSeries.current.detachPrimitive(priceMeasurementPrimitiveRef.current);
                } catch (error) {
                  console.warn('Error detaching price measurement primitive:', error);
                }
                priceMeasurementPrimitiveRef.current = null;
              }
            }}
          >
            {t('tview.clearDrawings')}
          </button>

          {/* Remove Selected Line Button */}
          <button
            disabled={!selectedLine}
            style={{
              padding: '6px 12px',
              fontSize: '11px',
              fontFamily: 'Orbitron, sans-serif',
              fontWeight: 600,
              letterSpacing: '0.05em',
              textTransform: 'uppercase',
              borderRadius: '8px',
              border: `2px solid ${selectedLine ? 'rgba(239, 68, 68, 0.5)' : 'var(--space-border-light)'}`,
              background: selectedLine
                ? 'linear-gradient(135deg, rgba(239, 68, 68, 0.2) 0%, rgba(220, 38, 38, 0.2) 100%)'
                : 'rgba(0, 212, 255, 0.05)',
              color: selectedLine ? '#f87171' : 'var(--space-tertiary)',
              cursor: selectedLine ? 'pointer' : 'not-allowed',
              opacity: selectedLine ? 1 : 0.5,
              transition: 'all 0.3s ease',
            }}
            onClick={() => {
              if (!selectedLine) return;
              if (horizontalLinesRef.current.length === 1) {
                deleteUserLines(lines.id).then((res: any) => {
                  console.log("res", res);
                }).catch((err: any) => {
                  console.error('Error deleting user lines:', err);
                });
                clearAllDrawings();
                setLines({ id: 0, h_lines: [], v_lines: [] });
                setHorizontalLines([]);
              } else {
                setHorizontalLines(horizontalLinesRef.current.filter(line => line !== selectedLine));
                horizontalLinesRef.current = horizontalLinesRef.current.filter(line => line !== selectedLine);
                console.log("horizontalLines", horizontalLinesRef.current);
                saveLines(horizontalLinesRef.current);
                setSelectedLine(null)
              }
            }}
          >
            {t('tview.remove')}
          </button>

          {/* Price Measurement Button */}
          <button
            style={{
              padding: '6px 12px',
              fontSize: '11px',
              fontFamily: 'Orbitron, sans-serif',
              fontWeight: 600,
              letterSpacing: '0.05em',
              textTransform: 'uppercase',
              borderRadius: '8px',
              border: `2px solid ${priceMeasurementMode ? 'rgba(251, 191, 36, 0.5)' : 'var(--space-border-light)'}`,
              background: priceMeasurementMode
                ? 'linear-gradient(135deg, rgba(251, 191, 36, 0.2) 0%, rgba(245, 158, 11, 0.2) 100%)'
                : 'rgba(0, 212, 255, 0.05)',
              color: priceMeasurementMode ? '#fbbf24' : 'var(--space-secondary)',
              cursor: 'pointer',
              transition: 'all 0.3s ease',
              boxShadow: priceMeasurementMode
                ? '0 0 15px rgba(251, 191, 36, 0.3)'
                : 'none',
            }}
            onClick={() => {
              // 清除趋势线预览线
              if (trendPreviewPrimitiveRef.current && candlestickSeries.current) {
                try {
                  candlestickSeries.current.detachPrimitive(trendPreviewPrimitiveRef.current);
                } catch (e) { /* ignore */ }
                trendPreviewPrimitiveRef.current = null;
              }
              if (drawingMode) {
                setDrawingMode(false);
              }
              const newMode = !priceMeasurementMode;
              setPriceMeasurementMode(newMode);
              setPriceMeasurementPoints([]);
              setPriceMeasurementResult(null);
              if (priceMeasurementPrimitiveRef.current && candlestickSeries.current) {
                try {
                  candlestickSeries.current.detachPrimitive(priceMeasurementPrimitiveRef.current);
                } catch (error) {
                  console.warn('Error detaching price measurement primitive:', error);
                }
                priceMeasurementPrimitiveRef.current = null;
              }
            }}
          >
            {priceMeasurementMode ? t('tview.exitMeasurement') : t('tview.priceMeasurement')}
          </button>

          {/* Locate to Order Button */}
          <button
            style={{
              padding: '6px 12px',
              fontSize: '11px',
              fontFamily: 'Orbitron, sans-serif',
              fontWeight: 600,
              letterSpacing: '0.05em',
              textTransform: 'uppercase',
              borderRadius: '8px',
              border: '2px solid rgba(34, 197, 94, 0.5)',
              background: 'linear-gradient(135deg, rgba(34, 197, 94, 0.2) 0%, rgba(22, 163, 74, 0.2) 100%)',
              color: '#4ade80',
              cursor: 'pointer',
              transition: 'all 0.3s ease',
            }}
            onClick={locateToOrder}
            title={t('tview.locateToOrder') || '定位到当前订单'}
          >
            {t('tview.locateToOrder') || '定位订单'}
          </button>

          {/* Delete Trend Line Button */}
          <button
            disabled={selectedTrendLine === null}
            style={{
              padding: '6px 12px',
              fontSize: '11px',
              fontFamily: 'Orbitron, sans-serif',
              fontWeight: 600,
              letterSpacing: '0.05em',
              textTransform: 'uppercase',
              borderRadius: '8px',
              border: `2px solid ${selectedTrendLine !== null ? 'rgba(239, 68, 68, 0.5)' : 'var(--space-border-light)'}`,
              background: selectedTrendLine !== null
                ? 'linear-gradient(135deg, rgba(239, 68, 68, 0.2) 0%, rgba(220, 38, 38, 0.2) 100%)'
                : 'rgba(0, 212, 255, 0.05)',
              color: selectedTrendLine !== null ? '#f87171' : 'var(--space-tertiary)',
              cursor: selectedTrendLine !== null ? 'pointer' : 'not-allowed',
              opacity: selectedTrendLine !== null ? 1 : 0.5,
              transition: 'all 0.3s ease',
            }}
            onClick={deleteSelectedTrendLine}
          >
            删除趋势线
          </button>
        </div>

        {/* Fullscreen Toggle */}
        <div style={{ marginLeft: isFullscreenRef.current && isLandscape ? 'auto' : 'auto' }}>
          <button
            style={{
              padding: '6px 16px',
              fontSize: '11px',
              fontFamily: 'Orbitron, sans-serif',
              fontWeight: 600,
              letterSpacing: '0.05em',
              textTransform: 'uppercase',
              borderRadius: '8px',
              border: '2px solid var(--space-accent)',
              background: 'var(--space-blue-dark)',
              color: 'var(--space-primary)',
              cursor: 'pointer',
              transition: 'all 0.3s ease',
              boxShadow: '0 0 15px var(--space-accent-glow)',
            }}
            onClick={toggleFullscreen}
          >
            {isFullscreenRef.current ? t('tview.exitFullscreen') : t('tview.fullscreen')}
          </button>
        </div>
      </div>

      {/* Chart Container */}
      <div
        ref={chartContainerRef}
        style={{
          position: 'relative',
          flex: 1,
          background: '#0a0e27',
          overflow: 'hidden',
        }}
      />
    </div>
  );
}
