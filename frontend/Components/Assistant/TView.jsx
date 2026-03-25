// pages/index.tsx
"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  createChart,
  CrosshairMode,
  CandlestickSeries,
  createSeriesMarkers,
  HistogramSeries,
} from "lightweight-charts";

import { getUserLines, createUserLines, updateUserLines, deleteUserLines } from "services/assistant.service";

import { TrendLine } from "./ChartPlugins/TrendLine";
import { HorizontalLine } from "./ChartPlugins/HorizontalLine";
import { PriceMeasurement } from "./ChartPlugins/PriceMeasurement";
import { getKlines } from "services/trade.service";

export default function TView({order}) {
  const chartContainerRef = useRef(null);
  const chartWrapperRef = useRef(null);
  const chart = useRef(null);
  const candlestickSeries = useRef(null);
  const [priceData, setPriceData] = useState([]);
  const [volumeData, setVolumeData] = useState([]);
  const [timeframe, setTimeframe] = useState("15m");
  const [drawingMode, setDrawingMode] = useState(false);
  const [drawingTool, setDrawingTool] = useState("horizontal"); // trendline, horizontal, vertical
  const [drawingPoints, setDrawingPoints] = useState([]);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [isLandscape, setIsLandscape] = useState(false);
  const [horizontalLines, setHorizontalLines] = useState([]);
  const [trendLines, setTrendLines] = useState([]);
  const [lines, setLines] = useState({id: 0, h_lines: [], v_lines: []});
  const [selectedLine, setSelectedLine] = useState(null);
  const [isLoadingData, setIsLoadingData] = useState(false);
  const [dataRange, setDataRange] = useState({ start: 0, end: 0 });
  const [chartInitialized, setChartInitialized] = useState(false);
  // Price measurement tool state
  const [priceMeasurementMode, setPriceMeasurementMode] = useState(false);
  const [priceMeasurementPoints, setPriceMeasurementPoints] = useState([]);
  const [priceMeasurementResult, setPriceMeasurementResult] = useState(null);
  const linesRef = useRef(lines);
  const horizontalLinesRef = useRef(horizontalLines);
  const horizontalPrimitivesRef = useRef([]); // Store primitive references
  const drawingModeRef   = useRef(drawingMode);
  const priceDataRef = useRef(priceData);
  const volumeDataRef = useRef(volumeData);
  const dataRangeRef = useRef(dataRange);
  const visibleRangeRef = useRef(null);
  const priceMeasurementModeRef = useRef(priceMeasurementMode);
  const priceMeasurementPointsRef = useRef(priceMeasurementPoints);
  const priceMeasurementPrimitiveRef = useRef(null);
  const isLoadingDataRef = useRef(isLoadingData);
  const histogramSeriesRef = useRef(null);
  const chartInitializedRef = useRef(false);

  useEffect(() => {
    linesRef.current = lines;
    horizontalLinesRef.current = horizontalLines;
  }, [lines, horizontalLines]);

  useEffect(() => {
    priceDataRef.current = priceData;
    volumeDataRef.current = volumeData;
    dataRangeRef.current = dataRange;
  }, [priceData, volumeData, dataRange]);

  useEffect(() => {
    isLoadingDataRef.current = isLoadingData;
  }, [isLoadingData]);

  useEffect(() => {
    chartInitializedRef.current = chartInitialized;
  }, [chartInitialized]);

  useEffect(() => {
    priceMeasurementModeRef.current = priceMeasurementMode;
  }, [priceMeasurementMode]);

  useEffect(() => {
    priceMeasurementPointsRef.current = priceMeasurementPoints;
  }, [priceMeasurementPoints]);

  useEffect(() => {
    drawingModeRef.current = drawingMode;
  }, [drawingMode]);

  // 动态加载更多数据的函数
  const loadMoreData = async (direction = 'earlier') => {
    if (!order || !order.symbol || isLoadingDataRef.current) return;
    
    try {
      const currentData = priceDataRef.current;
      const interval = timeframe.endsWith("m") ? 1000*60 : timeframe.endsWith("h") ? 1000*60*60 : 1000*60*60*24;
      const timeInterval = parseInt(timeframe.slice(0, -1)) * interval;

      if (direction === 'later' && priceDataRef.current[priceDataRef.current.length - 1].timestamp >  new Date().getTime() - timeInterval) {
        console.log('No more later data available');
        return;
      }

      setIsLoadingData(true);
      
      if (currentData.length === 0) return;
      
      let startTime, endTime;
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
      
      const newData = await getKlines(
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
      newData.forEach(item => {
        item.time = item.timestamp;
      });
      
      // 合并数据并排序
      let mergedPriceData;
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
      const volumes = uniqueData.map(item => ({
        time: item.timestamp, // 使用秒级时间戳保持一致性
        value: item.volume > 90071992547409.91 ? 90071992547409.91 : item.volume,
        color: item.close > item.open ? '#26a69a' : '#ef5350'
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
      
      
      // 图表还没初始化或更新失败，使用状态更新
      // setPriceData(uniqueData); 
      // setVolumeData(volumes);
      
      // if (chart.current && candlestickSeries.current && chartInitializedRef.current) {
      //   try {
      //     // 保存当前可视范围
      //     const currentVisibleRange = chart.current.timeScale().getVisibleRange();

      //     setDataRange({
      //       start: currentVisibleRange.from,
      //       end: currentVisibleRange.to
      //     });
          
      //   } catch (error) {
      //     console.warn('Error updating chart data directly:', error);
      //   }
      // }
      
      // // 更新数据范围
     
      
    } catch (error) {
      console.error(`Error loading ${direction} data:`, error);
    } finally {
      setIsLoadingData(false);
    }
  };
  
  useEffect(() => {
    if (!order || !order.order_id) return;

    setDrawingMode(false);
    setHorizontalLines([]);
    setTrendLines([]);
    setDrawingPoints([]);
    setLines({id: 0, h_lines: [], v_lines: []});

    const controller = new AbortController();

    const fetchUserLines = async () => {
        try {
          const res = await getUserLines(order.order_id, { signal: controller.signal });
          if (controller.signal.aborted) return;
          if (res && res.data) {
            setLines(res.data);
            // Handle empty or null h_lines
            const hLines = res.data.h_lines;
            if (hLines && typeof hLines === 'string' && hLines.trim()) {
              setHorizontalLines(hLines.split(",").filter(line => line.trim()));
            } else {
              setHorizontalLines([]);
            }
          }
        } catch (err) {
          if (err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED' || err?.message === 'canceled') return;
          
          // Handle 404 - User lines don't exist yet, which is normal for new orders
          if (err?.response?.status === 404) {
            console.log('User lines not found for order', order.order_id, '- this is normal for new orders');
            setLines({id: 0, h_lines: [], v_lines: []});
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

  const saveLines = async (newHorizontalLines) => {
    try {
      if (!order || !order.order_id) {
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
          order_id: order.order_id,
          h_lines: linesData
        });
        if (res && res.data) {
          setLines(res.data);
        }
      }
    } catch (error) {
      console.error('Error saving user lines:', error);
      // Show user-friendly error message
      if (error?.response?.status === 404) {
        console.log('Order not found, unable to save lines');
      } else if (error?.response?.status >= 500) {
        console.log('Server error, please try again later');
      }
    }
  }

  // 检测是否为移动设备和屏幕方向
  useEffect(() => {
    const checkMobileAndOrientation = () => {
      const mobile = window.innerWidth <= 768;
      let landscape = window.innerWidth > window.innerHeight;
      
      // 使用Screen Orientation API检测方向（如果可用）
      if (screen.orientation) {
        landscape = screen.orientation.type.includes('landscape');
      }
      
      setIsMobile(mobile);
      setIsLandscape(landscape);
    };
    
    const handleOrientationChange = () => {
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

  // 全屏切换功能
  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      // 进入全屏
      if (chartWrapperRef.current.requestFullscreen) {
        chartWrapperRef.current.requestFullscreen();
      } else if (chartWrapperRef.current.webkitRequestFullscreen) {
        chartWrapperRef.current.webkitRequestFullscreen();
      } else if (chartWrapperRef.current.msRequestFullscreen) {
        chartWrapperRef.current.msRequestFullscreen();
      }
      setIsFullscreen(true);
      
      // 自动切换到横屏模式
      if (screen.orientation && screen.orientation.lock) {
        screen.orientation.lock('landscape').catch(err => {
          console.log('横屏锁定失败:', err);
        });
      } else if (screen.lockOrientation) {
        screen.lockOrientation('landscape');
      } else if (screen.mozLockOrientation) {
        screen.mozLockOrientation('landscape');
      } else if (screen.msLockOrientation) {
        screen.msLockOrientation('landscape');
      }
    } else {
      // 退出全屏
      if (document.exitFullscreen) {
        document.exitFullscreen();
      } else if (document.webkitExitFullscreen) {
        document.webkitExitFullscreen();
      } else if (document.msExitFullscreen) {
        document.msExitFullscreen();
      }
      setIsFullscreen(false);
      
      // 解锁屏幕方向
      if (screen.orientation && screen.orientation.unlock) {
        screen.orientation.unlock();
      } else if (screen.unlockOrientation) {
        screen.unlockOrientation();
      } else if (screen.mozUnlockOrientation) {
        screen.mozUnlockOrientation();
      } else if (screen.msUnlockOrientation) {
        screen.msUnlockOrientation();
      }
    }
  };

  // 清除所有绘图
  useEffect(() => {
    return () => {
      clearAllDrawings();
    };
  }, [order]);

  // 监听全屏状态变化
  useEffect(() => {
    const handleFullscreenChange = () => {
      const wasFullscreen = isFullscreen;
      const newFullscreenState = !!document.fullscreenElement;
      setIsFullscreen(newFullscreenState);
      
      // 如果从全屏退出，解锁屏幕方向
      if (wasFullscreen && !newFullscreenState) {
        if (screen.orientation && screen.orientation.unlock) {
          screen.orientation.unlock();
        } else if (screen.unlockOrientation) {
          screen.unlockOrientation();
        } else if (screen.mozUnlockOrientation) {
          screen.mozUnlockOrientation();
        } else if (screen.msUnlockOrientation) {
          screen.msUnlockOrientation();
        }
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
  }, [isFullscreen]);
  
  // 处理鼠标点击事件，用于画线
  const handleChartClick = (param) => {
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

        // Convert time to seconds if it's in milliseconds
        const time = (typeof param.time === 'number' && param.time > 1e10) ? Math.floor(param.time / 1000) : param.time;
        const price = candlestickSeries.current.coordinateToPrice(param.point.y);
        if (price === null || price === undefined) return;

        const newPoint = { time, price };
        const newPoints = [...priceMeasurementPointsRef.current, newPoint];
        setPriceMeasurementPoints(newPoints);

        // If we have two points, calculate the measurement
        if (newPoints.length === 2) {
          // Calculate price difference and percentage change
          const priceDiff = newPoints[1].price - newPoints[0].price;
          const percentChange = ((newPoints[1].price - newPoints[0].price) / newPoints[0].price) * 100;

          // Store the result
          setPriceMeasurementResult({
            point1: newPoints[0],
            point2: newPoints[1],
            priceDiff,
            percentChange
          });

          // Create and attach the price measurement primitive
          if (chart.current && candlestickSeries.current) {
            // Remove existing measurement primitive if any
            if (priceMeasurementPrimitiveRef.current) {
              try {
                candlestickSeries.current.detachPrimitive(priceMeasurementPrimitiveRef.current);
                // chart.current.update();
              } catch (error) {
                console.warn('Error detaching existing price measurement primitive:', error);
              }
            }

            const measurement = new PriceMeasurement(chart.current, candlestickSeries.current, newPoints[0], newPoints[1]);
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
        const exist = horizontalLinesRef.current.filter(line => (line <= price * 1.005 && line >= price * 0.995));
        setSelectedLine(exist.length > 0 ? exist[0] : null);
        return;
      }
      
      console.log("handleChartClick", param);
          
      const time = param.time;
      const price = candlestickSeries.current.coordinateToPrice(param.point.y);
      if (price === null || price === undefined) return;
      
      if (drawingTool === "horizontal") {
        // const horizontal = new HorizontalLine(chart.current, candlestickSeries.current, price);
        const newHorizontalLines = [...horizontalLinesRef.current, price];
        setHorizontalLines(newHorizontalLines);
        saveLines(newHorizontalLines);

        setDrawingMode(false);
      }
      else if (drawingTool === "trendline") {
        const newPoint = { time, price };
        const newPoints = [...drawingPoints, newPoint];
        setDrawingPoints(newPoints);
        
        if (newPoints.length === 2 && chart.current && candlestickSeries.current) {
          setTrendLines([...trendLines, newPoints]);
          const trend = new TrendLine(chart.current, candlestickSeries.current, newPoints[0], newPoints[1]);
          candlestickSeries.current.attachPrimitive(trend);
          setDrawingPoints([]);
        }
      }
    } catch (error) {
      console.warn('Error handling chart click:', error);
    }
  };
  
  // 清除所有绘图
  const clearAllDrawings = () => {
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
        
        // Detach price measurement primitive if exists
        if (priceMeasurementPrimitiveRef.current) {
          try {
            candlestickSeries.current.detachPrimitive(priceMeasurementPrimitiveRef.current);
          } catch (error) {
            console.warn('Error detaching price measurement primitive:', error);
          }
          priceMeasurementPrimitiveRef.current = null;
        }
      }

      setHorizontalLines([]);
      setTrendLines([]);
      horizontalPrimitivesRef.current = [];
    } catch (error) {
      console.warn('Error clearing drawings:', error);
    }
  };

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
    const interval = timeframe.endsWith("m") ? 1000*60 : timeframe.endsWith("h") ? 1000*60*60 : 1000*60*60*24;
    const time = parseInt(timeframe.slice(0, -1)) * interval;
    const start_time = new Date(order.timestamp).getTime();
    const end_time = order.parent_order ? new Date(order.parent_order[0].timestamp).getTime() : new Date(order.timestamp).getTime();
    const count = (end_time - start_time) / time;
    const add = count > 1000 ? 100 : parseInt((1000-count)/2);

    const controller = new AbortController();

    const fetchData = async () => {
      let temp = [];
      let left = count+2*add;
      let start = start_time - add*time;
      let end = end_time + add*time;

      console.log("time", new Date(start).toLocaleString(), new Date(end).toLocaleString());
      while (left >= 1) {
        if (controller.signal.aborted) return;
        try {
          const data = await getKlines(
            order.symbol, 
            timeframe,
            start,
            end,
            { signal: controller.signal }
          );

          if (controller.signal.aborted) return;
          if (data.length == 0) {
            break;
          }
          start = data[data.length-1].timestamp + time;
          left -= data.length;
          data.map(item => {
            item.time = item.timestamp;
          });
          temp.push(...data);
        } catch (error) {
          if (error?.name === 'CanceledError' || error?.code === 'ERR_CANCELED' || error?.message === 'canceled') {
            return;
          }
          console.error('Error fetching price data:', error);
          break;
        }
      }
      if (controller.signal.aborted) return;
      setPriceData(temp);
      // 处理交易量数据
      const volumes = temp.map(item => ({
        time: item.timestamp, // 使用秒级时间戳保持一致性
        value: item.volume > 90071992547409.91 ? 90071992547409.91 : item.volume,
        color: item.close > item.open ? '#26a69a' : '#ef5350'
      }));
      setVolumeData(volumes);
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
        height: isFullscreen ? window.innerHeight - 64 : chartWrapperRef.current.clientHeight > 64 ? chartWrapperRef.current.clientHeight - 64 : window.innerHeight - 180,
        layout: {
          background: { color: "#ffffff" },
          textColor: "#000000",
        },
        grid: {
          vertLines: { color: "#eeeeee" },
          horzLines: { color: "#eeeeee" },
        },
        crosshair: {
          mode: CrosshairMode.Normal,
        },
        timeScale: {
          borderColor: "#cccccc",
          tickMarkFormatter: (time) => {
            const date = new Date(time);
            const year = date.getFullYear().toString();
            const month = (date.getMonth() + 1).toString().padStart(2, '0');
            const day = date.getDate().toString().padStart(2, '0');
            return `${year}-${month}-${day}`;
          }
        },
        localization: {
          timeFormatter: (time) => {
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
      
    const getMaxDecimalLength = (obj) => {
      return ['open', 'close', 'high', 'low']
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
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderVisible: false,
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
      priceFormat: {
        type: "price",
        precision: precision,
        minMove: 10**(-precision)
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
    
    // 标记图表已初始化
    setChartInitialized(true);
    
    if (order) {
      const interval = timeframe.endsWith("m") ? 1000*60 : timeframe.endsWith("h") ? 1000*60*60 : 1000*60*60*24;
      const time = timeframe == '1m' ? 0 : parseInt(timeframe.slice(0, -1)) * interval;
      let markers = [];
      if (order.parent_order) {
        markers = order.parent_order.map((o) => ({
          time: new Date(o.timestamp).getTime() - time,
          position: "aboveBar",
          color: o.opt === "开空" || o.opt === "平空" ? "#ef5350" : "#26a69a",
          shape: "arrowDown",
          text: `${o.opt} @ ${parseFloat(o.price).toFixed(precision)} @ ${(parseFloat(o.amount) * parseFloat(o.price)).toFixed(0)}`,
        }));
      } 

      createSeriesMarkers(candlestickSeries.current, markers);
    }
    if (visibleRange) {
      chart.current.timeScale().setVisibleRange(visibleRange);
    } else {
      chart.current.timeScale().fitContent();
    }
    // 添加点击事件监听器用于画线
    chart.current.subscribeClick(handleChartClick);
    
    // 监听可见范围变化，用于动态加载数据
    const handleVisibleTimeRangeChange = (newVisibleRange) => {
      if (!newVisibleRange || !priceDataRef.current.length || isLoadingDataRef.current) return;
        
      const currentData = priceDataRef.current;
      const dataStart = currentData[0].timestamp;
      const dataEnd = currentData[currentData.length - 1].timestamp;
      const visibleStart = newVisibleRange.from;
      const visibleEnd = newVisibleRange.to;

      // 计算时间间隔
      const interval = timeframe.endsWith("m") ? 1000*60 : timeframe.endsWith("h") ? 1000*60*60 : 1000*60*60*24;
      const timeInterval = parseInt(timeframe.slice(0, -1)) * interval;
      
      // 判断是否需要加载更早的数据（用户向左滚动到数据边界）
      const needEarlierData = visibleStart <= dataStart - 100*timeInterval; // 当可见范围接近数据开始时
      
      // 判断是否需要加载更新的数据（用户向右滚动到数据边界）
      const needLaterData = visibleEnd >= dataEnd - 100*timeInterval; // 当可见范围接近数据结束时
      
      if (needEarlierData) {
        console.log('Loading earlier data due to scroll');
        loadMoreData('earlier');
      } else if (needLaterData) {
        console.log('Loading later data due to scroll');
        loadMoreData('later');
      }
    };
    
    chart.current.timeScale().subscribeVisibleTimeRangeChange(handleVisibleTimeRangeChange);

    chart.current.timeScale().subscribeVisibleLogicalRangeChange((range) => {
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
          chart.current.unsubscribeClick(handleChartClick);
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
      }
    };
  }, [priceData, volumeData, timeframe, isFullscreen, isLandscape, order]);

  // 绘制水平线
  useEffect(() => {
    if (!chart.current || !candlestickSeries.current) return;
    
    try {
      console.log("Drawing horizontal lines", horizontalLinesRef.current);
      horizontalPrimitivesRef.current.forEach(
        primitive => candlestickSeries.current.detachPrimitive(primitive)
      );
      horizontalPrimitivesRef.current = [];
      
      for (const horizontalLine of horizontalLinesRef.current) {
        const horizontal = new HorizontalLine(chart.current, candlestickSeries.current, horizontalLine, {lineColor: selectedLine==horizontalLine ? "rgb(239, 189, 49)" : "rgb(19, 49, 243)", width: 1, style: 0});
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
  }, [horizontalLinesRef.current, selectedLine, candlestickSeries.current]);
  
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
  
  // 监听窗口大小变化
  useEffect(() => {
    const handleResize = () => {
      if (chart.current && chartWrapperRef.current && chartContainerRef.current) {
        try {
          // Calculate consistent height logic
          const height = isFullscreen 
            ? window.innerHeight - 64 
            : chartWrapperRef.current.clientHeight > 64 
              ? chartWrapperRef.current.clientHeight - 64 
              : window.innerHeight - 180;
          
          // Check if chart still exists and has resize method
          if (chart.current && typeof chart.current.resize === 'function') {
            chart.current.resize(chartContainerRef.current.clientWidth, height);
          }
        } catch (error) {
          console.warn('Error resizing chart:', error);
        }
      }
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [isFullscreen, isLandscape]);

  const timeframeOptions = [
    { value: "1m", label: "1分" },
    // { value: "3m", label: "3分" },
    { value: "5m", label: "5分" },
    { value: "15m", label: "15分" },
    { value: "1h", label: "1时" },
    { value: "4h", label: "4时" },
    { value: "1d", label: "1日" },
  ];
  
  const drawingTools = [
    // { value: "trendline", label: "趋势线" },
    { value: "horizontal", label: "水平线" },
    // { value: "price-measurement", label: "价格测量" },
  ];

  return (
    <div className={`bg-purple-200 w-full h-full`} ref={chartWrapperRef}>
      <div className={`flex ${isFullscreen && isLandscape ? 'mb-1' : 'mb-2'} space-x-1`}>
        <button
          className={`px-2 py-1 text-xs rounded ${
            priceMeasurementMode ? "bg-yellow-500 text-white" : "bg-gray-200 text-gray-700 hover:bg-gray-300"
          }`}
          onClick={() => {
            // Turn off drawing mode if it's on
            if (drawingMode) {
              setDrawingMode(false);
            }

            const newMode = !priceMeasurementMode;
            setPriceMeasurementMode(newMode);

            // Clear any existing points when toggling mode
            setPriceMeasurementPoints([]);
            setPriceMeasurementResult(null);

            // Remove existing measurement primitive when turning off measurement mode
            if (!newMode && priceMeasurementPrimitiveRef.current && candlestickSeries.current) {
              try {
                candlestickSeries.current.detachPrimitive(priceMeasurementPrimitiveRef.current);
              } catch (error) {
                console.warn('Error detaching price measurement primitive:', error);
              }
              priceMeasurementPrimitiveRef.current = null;
            }
          }}
        >
          {priceMeasurementMode ? "退出测量" : "价格测量"}
        </button>
        {timeframeOptions.map((option) => (
          <button
            key={option.value}
            className={`px-2 py-1 text-xs rounded ${
              timeframe === option.value
                ? "bg-purple-500 text-white"
                : "bg-gray-200 text-gray-700 hover:bg-gray-300"
            }`}
            onClick={() => {  
              setTimeframe(option.value);
            }}
          >
            {option.label}
          </button>
        ))}
        
          <button
            className="px-2 py-1 text-xs rounded bg-blue-500 text-white ml-auto"
            onClick={toggleFullscreen}
          >
            {isFullscreen ? "退出全屏" : "全屏"}
          </button>
      </div>
      
      <div className={`flex ${isFullscreen && isLandscape ? 'mb-1' : 'mb-2'} space-x-1`}>
        {/* <button
          className={`px-2 py-1 text-xs rounded ${
            drawingMode   ? "bg-green-500 text-white" : "bg-gray-200 text-gray-700 hover:bg-gray-300"
          }`}
          onClick={() => {
            // Turn off price measurement mode if it's on
            if (priceMeasurementMode) {
              setPriceMeasurementMode(false);
              setPriceMeasurementPoints([]);
              setPriceMeasurementResult(null);
            }
            setDrawingMode(!drawingMode);
          }}
        >
          {drawingMode ? "退出画线" : "开始画线"}
        </button> */}
        
        {drawingTools.map((tool) => (
          <button
            key={tool.value}
            // disabled={!drawingMode}
            className={`px-2 py-1 text-xs rounded ${
              drawingMode && drawingTool === tool.value
                ? "bg-purple-500 text-white"
                : "bg-gray-200 text-gray-700 hover:bg-gray-300"
            }`}
            onClick={() => {setDrawingTool(tool.value); setDrawingMode(true);}}
          >
            {tool.label}
          </button>
        ))}
        
        <button
          className="px-2 py-1 text-xs rounded bg-red-500 text-white"
          onClick={() => {
            deleteUserLines(lines.id).then(res => {
              console.log("res", res);
            }).catch(err => {
              console.error('Error deleting user lines:', err);
            });
            clearAllDrawings();
            setLines({id: 0, h_lines: [], v_lines: []});
            setHorizontalLines([]);
            // Also clear price measurement
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
          清除画线
        </button>
        <button
          className={`px-2 py-1 text-xs rounded ${
            selectedLine
              ? "bg-red-500 text-white"
              : "bg-gray-200 text-gray-700 hover:bg-gray-300"
          }`}
          disabled={!selectedLine}
          onClick={() => {
            if (!selectedLine) return;
            if (horizontalLinesRef.current.length === 1) {
              deleteUserLines(lines.id).then(res => {
                console.log("res", res);
              }).catch(err => {
                console.error('Error deleting user lines:', err);
              });
              clearAllDrawings();
              setLines({id: 0, h_lines: [], v_lines: []});
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
          删除
        </button>
      </div>
      
      {/* Floating price measurement result window */}
      {priceMeasurementResult && (
        <div className="fixed top-20 right-4 bg-white border-2 border-blue-400 rounded-lg shadow-xl z-50 min-w-72 backdrop-blur-sm bg-white/95">
          <div className="bg-gradient-to-r from-blue-600 to-blue-500 text-white px-4 py-3 rounded-t-lg flex justify-between items-center border-b border-blue-400">
            <div className="font-semibold flex items-center">
              <svg className="w-4 h-4 mr-2" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-11a1 1 0 10-2 0v3.586L7.707 9.293a1 1 0 00-1.414 1.414l3 3a1 1 0 001.414 0l3-3a1 1 0 00-1.414-1.414L11 10.586V7z" clipRule="evenodd" />
              </svg>
              价格测量
            </div>
            <button
              onClick={() => setPriceMeasurementResult(null)}
              className="text-white hover:text-gray-200 text-lg font-bold transition-colors"
            >
              ×
            </button>
          </div>
          <div className="p-4">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="text-gray-600 font-medium">起始价格:</div>
              <div className="font-mono text-right text-gray-800 font-semibold">{priceMeasurementResult.point1.price.toFixed(4)}</div>

              <div className="text-gray-600 font-medium">结束价格:</div>
              <div className="font-mono text-right text-gray-800 font-semibold">{priceMeasurementResult.point2.price.toFixed(4)}</div>

              <div className="text-gray-600 font-medium">价格差:</div>
              <div className={`font-mono text-right font-bold ${priceMeasurementResult.priceDiff >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                {priceMeasurementResult.priceDiff >= 0 ? '+' : ''}{priceMeasurementResult.priceDiff.toFixed(4)}
              </div>

              <div className="text-gray-600 font-medium">变化率:</div>
              <div className={`font-mono text-right font-bold ${priceMeasurementResult.percentChange >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                {priceMeasurementResult.percentChange >= 0 ? '+' : ''}{priceMeasurementResult.percentChange.toFixed(2)}%
              </div>

              <div className="text-gray-600 font-medium">价格区间:</div>
              <div className="font-mono text-right text-gray-700">
                {Math.min(priceMeasurementResult.point1.price, priceMeasurementResult.point2.price).toFixed(4)} - {Math.max(priceMeasurementResult.point1.price, priceMeasurementResult.point2.price).toFixed(4)}
              </div>

              <div className="text-gray-600 font-medium">时间跨度:</div>
              <div className="font-mono text-right text-gray-700">
                {Math.abs(priceMeasurementResult.point2.time - priceMeasurementResult.point1.time) / (1000 * 60 * 60 * 24)} 天
              </div>
            </div>
            <div className="mt-4 pt-3 border-t border-gray-200">
              <div className="text-xs text-gray-500 text-center">
                点击图表任意位置退出测量模式
              </div>
            </div>
          </div>
        </div>
      )}
      
      <div 
        ref={chartContainerRef} 
        className={`w-full ${isFullscreen ? 'mt-18' : ''}`}
      />
    </div>
  );
}
