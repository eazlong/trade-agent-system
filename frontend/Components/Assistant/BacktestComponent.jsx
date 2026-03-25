// BacktestComponent.jsx
"use client";

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createChart } from 'lightweight-charts';
import { Button, Group, Stack, Text, Select } from '@mantine/core';
import { DatePicker } from '@mantine/dates';
import { IconPlayerPlay, IconPlayerPause, IconPlayerSkipBack, IconPlayerSkipForward } from '@tabler/icons-react';
import { getKlines } from 'services/trade.service';

const BacktestComponent = ({ symbol, initialTimeframe = '15m' }) => {
  // Refs
  const chartRef = useRef(null);
  const chart = useRef(null);
  const series = useRef(null);
  const playInterval = useRef(null);
  
  // State
  const [data, setData] = useState([]);
  const [visibleIndex, setVisibleIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [startDate, setStartDate] = useState(null);
  const [timeframe, setTimeframe] = useState(initialTimeframe);
  const [isLoading, setIsLoading] = useState(false);
  const [showPicker, setShowPicker] = useState(true);

  // Timeframe options
  const timeframes = [
    { value: '1m', label: '1m' },
    { value: '5m', label: '5m' },
    { value: '15m', label: '15m' },
    { value: '1h', label: '1h' },
    { value: '4h', label: '4h' },
    { value: '1d', label: '1d' },
  ];

  // Load data
  const loadData = useCallback(async (date) => {
    if (!symbol || !date) return;
    
    setIsLoading(true);
    try {
      const interval = timeframe.endsWith('m') ? 60000 : 
                     timeframe.endsWith('h') ? 3600000 : 86400000;
      const batchSize = 1000;
      const time = new Date(date).getTime();
      
      const [before, after] = await Promise.all([
        getKlines(symbol, timeframe, Math.floor((time - (batchSize * interval)) / 1000), Math.floor(time / 1000)),
        getKlines(symbol, timeframe, Math.floor(time / 1000), Math.floor((time + (batchSize * interval)) / 1000))
      ]);

      const processKline = k => ({
        time: k[0] / 1000,
        open: +k[1], high: +k[2], low: +k[3], close: +k[4],
      });

      const combined = [...(before || []).map(processKline), ...(after || []).map(processKline)];
      setData(combined);
      setVisibleIndex(combined.findIndex(d => d.time * 1000 >= time) - 1);
      setShowPicker(false);
      
    } catch (error) {
      console.error('Error loading data:', error);
    } finally {
      setIsLoading(false);
    }
  }, [symbol, timeframe]);

  // Initialize chart
  useEffect(() => {
    if (!chartRef.current || showPicker) return;

    chart.current = createChart(chartRef.current, {
      width: chartRef.current.clientWidth,
      height: 500,
      layout: { backgroundColor: '#1a1a1a', textColor: '#d9d9d9' },
      grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
    });

    series.current = chart.current.addCandlestickSeries({
      upColor: '#26a69a', downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    });

    return () => chart.current?.remove();
  }, [showPicker]);

  // Update chart data
  useEffect(() => {
    if (!series.current || data.length === 0) return;
    
    const visible = data.slice(0, visibleIndex + 1);
    series.current.setData(visible);
    
    if (visible.length > 0 && chart.current) {
      chart.current.timeScale().setVisibleRange({
        from: visible[0].time - 1,
        to: visible[visible.length - 1].time + 1
      });
    }
  }, [data, visibleIndex]);

  // Playback controls
  const togglePlay = useCallback(() => {
    if (isPlaying) {
      clearInterval(playInterval.current);
      setIsPlaying(false);
    } else {
      setIsPlaying(true);
      playInterval.current = setInterval(() => {
        setVisibleIndex(prev => {
          if (prev < data.length - 1) return prev + 1;
          clearInterval(playInterval.current);
          setIsPlaying(false);
          return prev;
        });
      }, 1000);
    }
  }, [isPlaying, data.length]);

  const stepForward = useCallback(() => {
    if (visibleIndex < data.length - 1) {
      setVisibleIndex(prev => prev + 1);
    }
  }, [visibleIndex, data.length]);

  const stepBackward = useCallback(() => {
    if (visibleIndex > 0) {
      setVisibleIndex(prev => prev - 1);
    }
  }, [visibleIndex]);

  // Cleanup
  useEffect(() => {
    return () => {
      if (playInterval.current) clearInterval(playInterval.current);
    };
  }, []);

  if (showPicker) {
    return (
      <Stack spacing="md" style={{ maxWidth: '500px', margin: '0 auto', padding: '20px' }}>
        <h2>Start Backtest</h2>
        <Select
          label="Timeframe"
          value={timeframe}
          onChange={setTimeframe}
          data={timeframes}
          disabled={isLoading}
        />
        <DatePicker
          value={startDate}
          onChange={setStartDate}
          placeholder="Select start date"
          maxDate={new Date()}
          disabled={isLoading}
        />
        <Button 
          onClick={() => loadData(startDate)}
          disabled={!startDate || isLoading}
          loading={isLoading}
        >
          Start Backtest
        </Button>
      </Stack>
    );
  }

  return (
    <Stack spacing="md">
      <Group position="apart">
        <Group>
          <Button
            variant="outline"
            onClick={stepBackward}
            disabled={isPlaying || visibleIndex === 0}
            leftIcon={<IconPlayerSkipBack size={16} />}
          >
            Back
          </Button>
          <Button
            variant={isPlaying ? "filled" : "outline"}
            onClick={togglePlay}
            leftIcon={isPlaying ? <IconPlayerPause size={16} /> : <IconPlayerPlay size={16} />}
          >
            {isPlaying ? 'Pause' : 'Play'}
          </Button>
          <Button
            variant="outline"
            onClick={stepForward}
            disabled={isPlaying || visibleIndex >= data.length - 1}
            leftIcon={<IconPlayerSkipForward size={16} />}
          >
            Forward
          </Button>
          <Text>
            {data[visibleIndex] ? new Date(data[visibleIndex].time * 1000).toLocaleString() : ''} • 
            {visibleIndex + 1} / {data.length}
          </Text>
        </Group>
        <Select
          value={timeframe}
          onChange={setTimeframe}
          data={timeframes}
          disabled={isLoading}
          style={{ width: '100px' }}
        />
      </Group>
      <div ref={chartRef} style={{ width: '100%', height: '500px' }} />
    </Stack>
  );
};

export default BacktestComponent;
